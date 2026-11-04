"""Latest camera stream and independently reported source/detector health.

The monitor runs on the FastAPI event loop but never reads from Webots, waits for
TCP, or encodes JPEGs. All those operations are isolated by the Commit 1 receiver.
"""
import asyncio
import io
import os
import logging
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4
from PIL import Image

from app.api.schemas.vision import (
    CameraHealthView, DetectorHealthView, VisionStatus,
)
from app.vision.camera_receiver import CameraReceiver
from app.vision.person_detector import TrackedFrame, YoloPersonTracker
from app.vision.overlay import annotate_live_frame

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
        detector: YoloPersonTracker | None = None,
        max_inference_fps: float = 5.0,
        overlay_max_age_s: float = 0.35,
        detector_stale_after_s: float = 3.0,
    ) -> None:
        if min(stale_after_s, startup_grace_s, max_stream_fps, poll_period_s, max_inference_fps, overlay_max_age_s, detector_stale_after_s) <= 0:
            raise ValueError("vision timing parameters must be positive")
        if not (0 <= stream_width_px <= 4096) or not (stream_jpeg_quality == 0 or 1 <= stream_jpeg_quality <= 95):
            raise ValueError("invalid stream image width or quality")
        self.stream_width_px = stream_width_px
        self.stream_jpeg_quality = stream_jpeg_quality
        self.receiver = receiver
        self.detector = detector
        self.max_inference_fps = max_inference_fps
        self.overlay_max_age_s = overlay_max_age_s
        self.detector_stale_after_s = detector_stale_after_s
        self.session_id = "vision-session-" + uuid4().hex[:12]
        self.stale_after_s = stale_after_s
        self.startup_grace_s = startup_grace_s
        self.max_stream_fps = max_stream_fps
        self.poll_period_s = poll_period_s
        self._started_at_s: float | None = None
        self._camera_state = "STARTING"
        # A missing/uninstalled detector must not prevent raw camera streaming.
        self._detector_state = "UNAVAILABLE"
        self._last_inference_at: str | None = None
        self._last_inference_s: float | None = None
        self._latest_detection: TrackedFrame | None = None
        # Temporary diagnostic: opt in with SAR_TRACK_DEBUG=1 before launching uvicorn.
        self._track_debug_enabled = os.getenv("SAR_TRACK_DEBUG") == "1"
        self._last_track_log_s = float("-inf")
        self._last_track_ids: tuple[int, ...] | None = None
        self._last_track_generation: int | None = None
        self._detection_subscribers: set[asyncio.Queue[TrackedFrame]] = set()
        self._overlay_revision = 0
        self._last_rendered_overlay_revision = -1
        self._render_lock = asyncio.Lock()
        self._detector_task: asyncio.Task | None = None
        self._status_revision = 0
        self._rtl_triggered = False  # camera-loss attempt, not flight-mode confirmation
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

    def camera_ready(self) -> bool:
        """Authoritative source-frame freshness, independent of browser/YOLO."""
        if self._started_at_s is None:
            return False
        _, _, arrived_s = self.receiver.stats()
        return arrived_s is not None and 0 <= time.monotonic() - arrived_s <= self.stale_after_s

    def camera_source_age_s(self) -> float | None:
        """Total elapsed time since last arrival (or startup if none received)."""
        if self._started_at_s is None:
            return None
        _, _, arrived_s = self.receiver.stats()
        base_s = arrived_s if arrived_s is not None else self._started_at_s
        return max(0.0, time.monotonic() - base_s)

    def set_automatic_rtl_triggered(self, triggered: bool) -> None:
        if self._rtl_triggered != triggered:
            self._rtl_triggered = triggered
            self._status_revision += 1
            self._notify_status()

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

    @property
    def latest_detection(self) -> TrackedFrame | None:
        """Read-only latest tracked observation, with source JPEG for Commit 5."""
        return self._latest_detection

    def subscribe_detections(self) -> asyncio.Queue[TrackedFrame]:
        """Bounded observation feed for the future evidence/selection pipeline."""
        queue: asyncio.Queue[TrackedFrame] = asyncio.Queue(maxsize=16)
        self._detection_subscribers.add(queue)
        return queue

    def unsubscribe_detections(self, queue: asyncio.Queue[TrackedFrame]) -> None:
        self._detection_subscribers.discard(queue)

    def _set_detector_state(self, state: str) -> None:
        if self._detector_state != state:
            self._detector_state = state
            self._status_revision += 1
            self._notify_status()

    def _publish_detection(self, result: TrackedFrame) -> None:
        self._latest_detection = result
        self._overlay_revision += 1
        if self._track_debug_enabled:
            now_s = time.monotonic()
            track_ids = tuple(sorted(
                person.track_id if person.track_id is not None else -1
                for person in result.persons
            ))
            if (track_ids != self._last_track_ids or
                result.tracking_generation != self._last_track_generation or
                now_s - self._last_track_log_s >= 1.0):
                LOG.info(
                    "YOLO track audit: generation=%s frame=%s tracks=%s",
                    result.tracking_generation, result.frame.frame_id,
                    [(person.track_id, round(person.confidence, 2)) for person in result.persons],
                )
                self._last_track_log_s = now_s
                self._last_track_ids = track_ids
                self._last_track_generation = result.tracking_generation
        for queue in tuple(self._detection_subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(result)

    def _fresh_persons(self, frame):
        result = self._latest_detection
        if result is None or self._detector_state != "LIVE":
            return ()
        reference = result.frame
        if (frame.source_id != reference.source_id or frame.frame_id < reference.frame_id
            or (frame.width, frame.height) != (reference.width, reference.height)):
            return ()
        difference_ms = frame.capture_unix_ms - reference.capture_unix_ms
        if not 0 <= difference_ms <= self.overlay_max_age_s * 1000:
            return ()
        return result.persons

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
        if self.detector is not None:
            self._set_detector_state("STARTING")
        await self.poll_once()
        self._task = asyncio.create_task(self._run(), name="vision-monitor")
        if self.detector is not None:
            self._detector_task = asyncio.create_task(self._run_detector(), name="vision-person-tracker")

    async def stop(self) -> None:
        if self._detector_task is not None:
            self._detector_task.cancel()
            await asyncio.gather(self._detector_task, return_exceptions=True)
            self._detector_task = None
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

    async def _run_detector(self) -> None:
        """Sequential inference in a worker thread; never block MAVLink/event loop."""
        assert self.detector is not None
        processed_count = -1
        previous_frame = None
        last_started_s = -1e30
        while True:
            try:
                await asyncio.to_thread(self.detector.initialize)
                break
            except Exception as exc:
                LOG.warning("Person detector startup unavailable; raw camera continues: %s", exc)
                self._set_detector_state("UNAVAILABLE")
                await asyncio.sleep(10.0)
                self._set_detector_state("STARTING")
        while True:
            count, _, arrival_s = self.receiver.stats()
            now_s = time.monotonic()
            if (self._camera_state != "LIVE" or arrival_s is None or
                now_s - arrival_s > self.stale_after_s or count == processed_count or
                now_s - last_started_s < 1.0 / self.max_inference_fps):
                await asyncio.sleep(self.poll_period_s)
                continue
            frame = self.receiver.latest()
            if frame is None:
                await asyncio.sleep(self.poll_period_s)
                continue
            processed_count = count
            # A gap or a changed source invalidates persistent tracker identities.
            if previous_frame is not None and (
                frame.source_id != previous_frame.source_id or
                frame.frame_id <= previous_frame.frame_id or
                not 0 <= frame.capture_unix_ms - previous_frame.capture_unix_ms <= 2000
            ):
                self.detector.reset()
            previous_frame = frame
            last_started_s = time.monotonic()
            try:
                result = await asyncio.to_thread(self.detector.process, frame)
            except Exception as exc:
                LOG.warning("Person detector inference failed; raw camera continues: %s", exc)
                self._latest_detection = None
                self._overlay_revision += 1
                self._set_detector_state("UNAVAILABLE")
                self.detector.reset()
                await asyncio.sleep(1.0)
                continue
            self._last_inference_s = time.monotonic()
            self._last_inference_at = _utc_string(datetime.now(timezone.utc))
            self._set_detector_state("LIVE")
            self._publish_detection(result)
            # Render at once if the stream FPS cap allows; otherwise the normal
            # monitor picks up the revision on its next pass.
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
        if self.detector is not None and self._last_inference_s is not None and (
            now - self._last_inference_s > self.detector_stale_after_s
        ):
            self._set_detector_state("UNAVAILABLE")
        if state != self._camera_state:
            self._camera_state = state
            self._status_revision += 1
            self._notify_status()
            # Notify streaming clients immediately when the source becomes stale.
            async with self._frame_condition:
                self._frame_condition.notify_all()

        if state != "LIVE":
            return
        # Monitor and detector tasks can both call poll_once. Serialize image
        # preparation so a slower encode cannot overwrite a newer stream frame.
        async with self._render_lock:
            now = time.monotonic()
            if count == self._last_source_count and self._overlay_revision == self._last_rendered_overlay_revision:
                return
            if self._last_output_at_s is not None and (
                now - self._last_output_at_s < (1.0 / self.max_stream_fps)
            ):
                return
            frame = self.receiver.latest()
            if frame is None:
                return
            persons = self._fresh_persons(frame)
            try:
                if persons:
                    rendered = await asyncio.to_thread(annotate_live_frame, frame.jpeg_bytes, persons)
                else:
                    rendered = frame.jpeg_bytes
                if self.stream_width_px or self.stream_jpeg_quality:
                    rendered = await asyncio.to_thread(
                        _prepare_jpeg, rendered, self.stream_width_px, self.stream_jpeg_quality
                    )
            except Exception as exc:
                LOG.warning("Camera stream image rendering failed; using raw source: %s", exc)
                rendered = frame.jpeg_bytes
            # If the camera went unavailable during image processing, never
            # resurrect an old JPEG as a supposedly live stream frame.
            if self._camera_state != "LIVE":
                return
            self._last_source_count = count
            self._last_rendered_overlay_revision = self._overlay_revision
            self._last_output_at_s = now
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
