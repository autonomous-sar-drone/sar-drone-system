from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.websockets.events import router as events_router
from app.runtime.telemetry_hub import TelemetryHub
from app.vision.candidate_store import CandidateStore
from test_candidate_store import evidence


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.telemetry_hub = TelemetryHub()
    app.state.candidate_store = CandidateStore("vision-session-candidate")
    yield


def test_websocket_candidate_publish_review_and_cleanup():
    app = FastAPI(lifespan=lifespan)
    app.include_router(events_router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as ws:
            store = app.state.candidate_store
            candidate = client.portal.call(lambda: store.publish(**evidence()))
            first = ws.receive_json()
            assert first["type"] == "detection_update"
            assert first["data"]["sessionId"] == "vision-session-candidate"
            assert first["data"]["revision"] == 1
            assert first["data"]["candidate"]["candidateId"] == candidate.candidate_id
            assert first["data"]["candidate"]["estimatedLocation"]["quality"] == "APPROXIMATE"

            client.portal.call(store.review, candidate.candidate_id, "DISMISS")
            changed = ws.receive_json()
            assert changed["type"] == "detection_update"
            assert changed["data"]["revision"] == 2
            assert changed["data"]["candidate"]["status"] == "DISMISSED"
            ws.close()
        assert not app.state.candidate_store._subscribers


# The real application has vision-status and candidate updates on the same socket.
@asynccontextmanager
async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.vision.vision_runtime import VisionRuntime
    from test_vision_runtime import FakeCameraReceiver
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera)
    app.state.telemetry_hub = TelemetryHub()
    app.state.vision_runtime = runtime
    app.state.candidate_store = CandidateStore(runtime.session_id)
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def test_vision_health_and_candidate_updates_share_one_socket():
    app = FastAPI(lifespan=combined_lifespan)
    app.include_router(events_router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as ws:
            status = ws.receive_json()
            assert status["type"] == "vision_status"
            candidate = client.portal.call(lambda: app.state.candidate_store.publish(**evidence()))
            update = ws.receive_json()
            assert update["type"] == "detection_update"
            assert update["data"]["sessionId"] == status["data"]["sessionId"]
            assert update["data"]["candidate"]["candidateId"] == candidate.candidate_id
            ws.close()
        assert not app.state.candidate_store._subscribers
