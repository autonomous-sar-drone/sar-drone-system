import asyncio
from io import BytesIO
import time

from PIL import Image
import pytest

from app.vision.camera_frame import CapturedFrame
from app.vision.vision_runtime import VisionRuntime, _prepare_jpeg


def sample_jpeg(color=(180, 40, 20)):
    output = BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="JPEG")
    return output.getvalue()


class FakeCameraReceiver:
    def __init__(self):
        self.count = 0
        self.connected = False
        self.arrived_at = None
        self.frame = None

    def stats(self):
        return self.count, self.connected, self.arrived_at

    def latest(self):
        return self.frame

    def push(self):
        self.count += 1
        self.connected = True
        self.arrived_at = time.monotonic()
        self.frame = CapturedFrame(
            source_id="test-camera", frame_id=self.count, simulation_time_s=1.0,
            capture_unix_ms=1793500000000 + self.count, width=8, height=6,
            horizontal_fov_rad=1.0, jpeg_bytes=sample_jpeg(),
        )


@pytest.mark.asyncio
async def test_camera_state_lifecycle_sparse_events_and_recovery():
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera)
    await runtime.start()
    queue, initial = runtime.subscribe()
    try:
        assert initial.camera.state == "STARTING"
        assert initial.detector.state == "UNAVAILABLE"  # detector is not built yet
        assert initial.revision == 0
        assert initial.camera_outage_seconds is None
        assert initial.automatic_rtl_triggered is False
        camera.push()
        await runtime.poll_once()
        changed = queue.get_nowait()
        assert changed.camera.state == "LIVE"
        assert changed.revision == 1
        version, payload = await runtime.next_stream_frame(-1)
        assert version > 0 and payload == camera.frame.jpeg_bytes
        camera.push()
        await runtime.poll_once()
        assert runtime.status().revision == 1  # new frames are not status events
        assert queue.empty()
        camera.arrived_at -= 6.0
        await runtime.poll_once()
        lost = queue.get_nowait()
        assert lost.camera.state == "UNAVAILABLE"
        assert lost.camera_outage_seconds >= 5.0
        assert lost.revision == 2
        assert lost.automatic_rtl_triggered is False  # Commit 7, not implemented
        _, jpeg = await runtime.next_stream_frame(-1)
        assert jpeg is None  # stale JPEG must never be shown as live
        camera.push()
        await runtime.poll_once()
        recovered = queue.get_nowait()
        assert recovered.camera.state == "LIVE"
        assert recovered.revision == 3
        assert recovered.camera_outage_seconds is None
        assert runtime.status().session_id == initial.session_id
    finally:
        runtime.unsubscribe(queue)
        await runtime.stop()


@pytest.mark.asyncio
async def test_no_camera_transitions_to_unavailable_without_rtl():
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera, startup_grace_s=0.01)
    await runtime.start()
    try:
        await asyncio.sleep(0.02)
        await runtime.poll_once()
        status = runtime.status()
        assert status.camera.state == "UNAVAILABLE"
        assert status.camera_outage_seconds >= 0.01
        assert status.automatic_rtl_triggered is False
    finally:
        await runtime.stop()


def test_image_transform_keeps_color_and_aspect_ratio():
    original = sample_jpeg()
    assert _prepare_jpeg(original, 0, 0) is original  # no extra JPEG work by default
    resized = _prepare_jpeg(original, 16, 80)
    with Image.open(BytesIO(resized)) as image:
        assert image.size == (16, 12)
        r, g, b = image.convert("RGB").getpixel((4, 4))
        assert r > g and r > b
    with pytest.raises(ValueError):
        VisionRuntime(FakeCameraReceiver(), stream_jpeg_quality=150)
