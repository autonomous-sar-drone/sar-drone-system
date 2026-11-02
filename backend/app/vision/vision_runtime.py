"""Latest camera stream and independently reported source/detector health.

The monitor runs on the FastAPI event loop but never reads from Webots, waits for
TCP, or encodes JPEGs. All those operations are isolated by the Commit 1 receiver.
"""
import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4
from PIL import Image

from app.api.schemas.vision import (
    CameraHealthView, DetectorHealthView, VisionStatus,
)
from app.vision.camera_receiver import CameraReceiver

LOG = logging.getLogger(__name__)


def _prepare_jpeg(jpeg: bytes, width_px: int, quality: int) -> bytes:
    if width_px == 0 and quality == 0:
        return jpeg
    with Image.open(io.BytesIO(jpeg)) as image:
        image = image.convert("RGB")
        if width_px and image.width != width_px:
            height_px = max(1, round(image.height * width_px / image.width))
            image = image.resize((width_px, height_px), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality or 85)
        return output.getvalue()


def _utc_string(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class VisionRuntime:
    def __init__(
        self,
        receiver: CameraReceiver,
        *,
        stale_after_s: float = 2.5,
        startup_grace_s: float = 3.0,
        max_stream_fps: float = 10.0,
        poll_period_s: float = 0.05,
        stream_width_px: int = 0,
        stream_jpeg_quality: int = 0,
    ) -> None:
        if min(stale_after_s, startup_grace_s, max_stream_fps, poll_period_s) <= 0:
            raise ValueError("vision timing parameters must be positive")
        if not (0 <= stream_width_px <= 4096) or not (stream_jpeg_quality == 0 or 1 <= stream_jpeg_quality <= 95):
            raise ValueError("invalid stream image width or quality")
        self.stream_width_px = stream_width_px
        self.stream_jpeg_quality = stream_jpeg_quality
        self.receiver = receiver
        self.session_id = "vision-session-" + uuid4().hex[:12]
        self.stale_after_s = stale_after_s
        self.startup_grace_s = startup_grace_s
        self.max_stream_fps = max_stream_fps
        self.poll_period_s = poll_period_s
        self._started_at_s: float | None = None
        self._camera_state = "STARTING"
        # No detector exists until the YOLO commit; do not report fake inference.
        self._detector_state = "UNAVAILABLE"
        self._last_inference_at: str | None = None
        self._status_revision = 0
        self._rtl_triggered = False  # the mission watchdog is introduced in Commit 7
        self._subscribers: set[asyncio.Queue[VisionStatus]] = set()
        self._frame_condition = asyncio.Condition()
        self._stream_version = 0
        self._stream_jpeg: bytes | None = None
        self._last_source_count = -1
        self._last_output_at_s: float | None = None
        self._task: asyncio.Task | None = None

    @property
    def camera_state(self) -> str:
        return self._camera_state

    def status(self) -> VisionStatus:
        now_s = time.monotonic()
        _, _, arrived_s = self.receiver.stats()
        now_utc = datetime.now(timezone.utc)
        last_frame_at = None
        if arrived_s is not None:
            # The receiver records monotonic arrival time; convert its age to UTC.
            last_frame_at = _utc_string(now_utc - timedelta(seconds=max(0, now_s - arrived_s)))

        outage_s = None
        if self._camera_state == "UNAVAILABLE":
            base_s = arrived_s if arrived_s is not None else self._started_at_s
            if base_s is not None:
                outage_s = round(max(0.0, now_s - base_s), 3)

        return VisionStatus(
            sessionId=self.session_id,
            revision=self._status_revision,
            timestamp=_utc_string(now_utc),
            camera=CameraHealthView(state=self._camera_state, lastFrameAt=last_frame_at),
            detector=DetectorHealthView(state=self._detector_state, lastInferenceAt=self._last_inference_at),
            cameraOutageSeconds=outage_s,
            automaticRtlTriggered=self._rtl_triggered,
        )

    def subscribe(self) -> tuple[asyncio.Queue[VisionStatus], VisionStatus]:
        queue: asyncio.Queue[VisionStatus] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue, self.status()

    def unsubscribe(self, queue: asyncio.Queue[VisionStatus]) -> None:
        self._subscribers.discard(queue)

    def _notify_status(self) -> None:
        snapshot = self.status()
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("vision runtime already started")
        self._started_at_s = time.monotonic()
        await self.poll_once()
        self._task = asyncio.create_task(self._run(), name="vision-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        async with self._frame_condition:
            self._frame_condition.notify_all()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_period_s)
            await self.poll_once()

    async def poll_once(self) -> None:
        """Public for deterministic tests; normal runtime calls it automatically."""
        now = time.monotonic()
        count, connected, arrived_at_s = self.receiver.stats()
        # A TCP connection is not proof of fresh frames; frame recency is.
        if arrived_at_s is not None and now - arrived_at_s <= self.stale_after_s:
            state = "LIVE"
        elif arrived_at_s is None and self._started_at_s is not None and (
            now - self._started_at_s < self.startup_grace_s
        ):
            state = "STARTING"
        else:
            state = "UNAVAILABLE"
        if state != self._camera_state:
            self._camera_state = state
            self._status_revision += 1
            self._notify_status()
            # Notify streaming clients immediately when the source becomes stale.
            async with self._frame_condition:
                self._frame_condition.notify_all()

        if state != "LIVE":
            return
        if count == self._last_source_count:
            return
        if self._last_output_at_s is not None and (
            now - self._last_output_at_s < (1.0 / self.max_stream_fps)
        ):
            return
        frame = self.receiver.latest()
        if frame is None:
            return
        self._last_source_count = count
        try:
            rendered = await asyncio.to_thread(
                _prepare_jpeg, frame.jpeg_bytes, self.stream_width_px, self.stream_jpeg_quality
            ) if (self.stream_width_px or self.stream_jpeg_quality) else frame.jpeg_bytes
        except Exception as exc:
            LOG.warning("Camera JPEG re-encoding failed, skipping frame: %s", exc)
            return
        self._last_output_at_s = now
        # Source produces a full-color JPEG. Passthrough avoids extra image
        # processing on the mission/navigation event loop.
        async with self._frame_condition:
            self._stream_jpeg = rendered
            self._stream_version += 1
            self._frame_condition.notify_all()

    async def next_stream_frame(self, after_version: int, timeout_s: float = 0.5) -> tuple[int, bytes | None]:
        async with self._frame_condition:
            if self._camera_state != "LIVE":
                return self._stream_version, None
            if self._stream_version > after_version and self._stream_jpeg is not None:
                return self._stream_version, self._stream_jpeg
            try:
                await asyncio.wait_for(
                    self._frame_condition.wait_for(
                        lambda: self._stream_version > after_version or self._camera_state != "LIVE"
                    ), timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                pass
            if self._camera_state != "LIVE" or self._stream_version <= after_version:
                return self._stream_version, None
            return self._stream_version, self._stream_jpeg
