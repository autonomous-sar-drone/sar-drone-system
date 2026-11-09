"""Backend integration without YOLO weights, SITL, or Webots.

Uses a frame-linked detector fake with real runtime, publication worker, store,
HTTP endpoints and candidate update serialization. Existing independent route
tests cover WebSocket send/receive, without adding a flaky close race here.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.routes.detections import router as detection_routes
from app.api.schemas.detections import candidate_event
from app.runtime.telemetry_hub import TelemetryHub
from app.vision.camera_frame import CapturedFrame
from app.vision.candidate_store import CandidateStore
from app.vision.candidate_publisher import CandidatePublisher, CandidatePublicationWorker
from app.vision.person_detector import PersonBox, TrackedFrame
from app.vision.vision_runtime import VisionRuntime


class Camera:
    def __init__(self):
        self.lock = threading.Lock()
        self.count = 0
        self.frame = None
        self.last_arrival = None

    def stats(self):
        with self.lock:
            return (self.count, self.frame is not None, self.last_arrival)

    def latest(self):
        with self.lock:
            return self.frame

    def push(self, capture_ms):
        with self.lock:
            self.count += 1
            b = BytesIO()
            Image.new('RGB', (120, 90), (200, 180, 140)).save(b, format='JPEG')
            self.frame = CapturedFrame(
                source_id='test-source', frame_id=self.count,
                simulation_time_s=self.count / 5,
                capture_unix_ms=capture_ms,
                width=120, height=90, horizontal_fov_rad=1.0,
                jpeg_bytes=b.getvalue(),
            )
            self.last_arrival = time.monotonic()


class Detector:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.tracking_generation = 0

    def initialize(self):
        pass

    def reset(self):
        self.tracking_generation += 1

    def process(self, frame):
        with self.lock:
            self.calls += 1
        return TrackedFrame(frame, (
            PersonBox(15, 20, 37, 65, 0.91, 8),
            PersonBox(78, 22, 103, 70, 0.83, 10),
        ), self.tracking_generation)


def until(predicate, timeout_s=3):
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('timed out waiting for detector/candidate pipeline')
        time.sleep(0.01)


def test_actual_runtime_to_http_and_events_preserves_contract():
    camera = Camera()
    detector = Detector()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = VisionRuntime(camera, detector=detector, max_inference_fps=100,
                                max_stream_fps=100, poll_period_s=0.005)
        store = CandidateStore(runtime.session_id)
        worker = CandidatePublicationWorker(runtime, CandidatePublisher(store))
        app.state.telemetry_hub = TelemetryHub()
        app.state.vision_runtime = runtime
        app.state.candidate_store = store
        worker.start()
        try:
            await runtime.start()
            yield
        finally:
            await runtime.stop()
            await worker.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(detection_routes)

    with TestClient(app) as client:
        assert client.get('/api/detections').json()['candidates'] == []
        store = app.state.candidate_store
        event_queue = store.subscribe()
        try:
            for index, ms in enumerate((0, 500, 1050)):
                camera.push(1793700000000 + ms)
                until(lambda: detector.calls >= index + 1)
            until(lambda: client.get('/api/detections').json()['revision'] == 2)
            observed = [candidate_event(event_queue.get_nowait()).model_dump(
                mode='json', by_alias=True) for _ in range(2)]
            assert all(item['type'] == 'detection_update' for item in observed)
            assert [item['data']['revision'] for item in observed] == [1, 2]
            assert len({item['data']['candidate']['candidateId'] for item in observed}) == 2
        finally:
            store.unsubscribe(event_queue)

        result = client.get('/api/detections').json()
        assert result['revision'] == 2
        assert [item['candidateNumber'] for item in result['candidates']] == [1, 2]
        assert all(item['estimatedLocation'] is None for item in result['candidates'])
        for item in result['candidates']:
            snapshot = client.get(item['snapshotUrl'])
            assert snapshot.status_code == 200
            with Image.open(BytesIO(snapshot.content)) as image:
                assert image.size == (120, 90)
        chosen = result['candidates'][0]
        response = client.post(f"/api/detections/{chosen['candidateId']}/review",
                               json={'decision': 'CONFIRM'})
        assert response.status_code == 200
        assert response.json()['candidate']['status'] == 'CONFIRMED'
        assert client.get('/api/detections').json()['revision'] == 3
