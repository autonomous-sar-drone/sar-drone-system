import asyncio
from dataclasses import replace
from io import BytesIO

from PIL import Image
import pytest

from app.vision.person_detector import PersonBox, TrackedFrame
from app.vision.vision_runtime import VisionRuntime
from test_vision_runtime import FakeCameraReceiver


class FakeDetector:
    def __init__(self, fail=False):
        self.calls = 0
        self.initialized = 0
        self.resets = 0
        self.fail = fail

    def initialize(self):
        self.initialized += 1

    def reset(self):
        self.resets += 1

    def process(self, frame):
        self.calls += 1
        if self.fail:
            raise ValueError("simulated inference failure")
        return TrackedFrame(frame, (PersonBox(1, 1, 7, 5, 0.92, 5),))


async def until(predicate, timeout_s=1.5):
    async def check():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(check(), timeout=timeout_s)


@pytest.mark.asyncio
async def test_live_boxes_and_health_separate_from_camera():
    camera = FakeCameraReceiver()
    detector = FakeDetector()
    runtime = VisionRuntime(camera, detector=detector, max_stream_fps=100, max_inference_fps=100)
    await runtime.start()
    events, initial = runtime.subscribe()
    queue = runtime.subscribe_detections()
    try:
        assert initial.detector.state == "STARTING"
        camera.push()
        await runtime.poll_once()
        await until(lambda: runtime.status().detector.state == "LIVE")
        assert runtime.status().camera.state == "LIVE"
        assert runtime.status().detector.last_inference_at is not None
        assert detector.calls == 1
        tracked = queue.get_nowait()
        assert tracked.frame.frame_id == 1
        assert tracked.persons[0].track_id == 5
        await asyncio.sleep(0.02)
        await runtime.poll_once()
        _, jpeg = await runtime.next_stream_frame(-1)
        assert jpeg is not None and jpeg != camera.frame.jpeg_bytes
        with Image.open(BytesIO(jpeg)) as image:
            assert image.size == (8, 6)
        # Multiple browser clients consume the same encoded stream and do not
        # trigger another model inference.
        await runtime.next_stream_frame(-1)
        assert detector.calls == 1
        states = []
        while not events.empty():
            states.append(events.get_nowait().detector.state)
        assert "LIVE" in states
    finally:
        runtime.unsubscribe(events)
        runtime.unsubscribe_detections(queue)
        await runtime.stop()


@pytest.mark.asyncio
async def test_overlay_dropped_after_original_frame_is_too_old():
    camera = FakeCameraReceiver()
    detector = FakeDetector()
    runtime = VisionRuntime(camera, detector=detector, max_stream_fps=100,
                            max_inference_fps=1, overlay_max_age_s=0.25)
    await runtime.start()
    try:
        camera.push()
        await runtime.poll_once()
        await until(lambda: runtime.status().detector.state == "LIVE")
        # Next capture is 500 ms later; old boxes must not be shifted onto it.
        await asyncio.sleep(0.02)
        camera.push()
        camera.frame = replace(camera.frame, capture_unix_ms=camera.frame.capture_unix_ms + 500)
        await runtime.poll_once()
        _, jpeg = await runtime.next_stream_frame(-1)
        assert jpeg == camera.frame.jpeg_bytes
        assert detector.calls == 1
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_detector_failure_does_not_break_source_stream():
    camera = FakeCameraReceiver()
    detector = FakeDetector(fail=True)
    runtime = VisionRuntime(camera, detector=detector, max_stream_fps=100)
    await runtime.start()
    try:
        camera.push()
        await runtime.poll_once()
        await until(lambda: detector.calls >= 1)
        await until(lambda: runtime.status().detector.state == "UNAVAILABLE")
        assert runtime.status().camera.state == "LIVE"
        _, raw = await runtime.next_stream_frame(-1)
        assert raw == camera.frame.jpeg_bytes
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_tracking_reset_after_source_switch_and_gap():
    camera = FakeCameraReceiver()
    detector = FakeDetector()
    runtime = VisionRuntime(camera, detector=detector, max_stream_fps=100,
                            max_inference_fps=100)
    await runtime.start()
    try:
        camera.push()
        await runtime.poll_once()
        await until(lambda: detector.calls == 1)
        await asyncio.sleep(0.02)
        camera.push()
        camera.frame = replace(camera.frame, source_id="other-camera")
        await runtime.poll_once()
        await until(lambda: detector.calls == 2)
        assert detector.resets == 1
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_unavailable_model_fails_open_for_camera(monkeypatch):
    camera = FakeCameraReceiver()

    class NoModel:
        def initialize(self):
            raise ImportError("ultralytics is not installed")

    runtime = VisionRuntime(camera, detector=NoModel())
    await runtime.start()
    try:
        camera.push()
        await runtime.poll_once()
        await until(lambda: runtime.status().detector.state == "UNAVAILABLE")
        assert runtime.status().camera.state == "LIVE"
        _, jpeg = await runtime.next_stream_frame(-1)
        assert jpeg == camera.frame.jpeg_bytes
    finally:
        await runtime.stop()
