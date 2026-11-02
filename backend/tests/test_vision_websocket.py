from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.websockets.events import router as events_router
from app.runtime.telemetry_hub import TelemetryHub
from app.vision.vision_runtime import VisionRuntime
from test_vision_runtime import FakeCameraReceiver


@asynccontextmanager
async def vision_socket_lifespan(app: FastAPI) -> AsyncIterator[None]:
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera)
    app.state.telemetry_hub = TelemetryHub()
    app.state.vision_runtime = runtime
    app.state.test_camera = camera
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def test_existing_socket_bootstrap_and_status_transition():
    app = FastAPI(lifespan=vision_socket_lifespan)
    app.include_router(events_router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as ws:
            initial = ws.receive_json()
            assert initial["type"] == "vision_status"
            assert initial["data"]["camera"]["state"] == "STARTING"
            assert initial["data"]["detector"]["state"] == "UNAVAILABLE"
            camera = app.state.test_camera
            runtime = app.state.vision_runtime
            def receive_new_frame():
                camera.push()
                return runtime.poll_once()
            client.portal.call(receive_new_frame)
            changed = ws.receive_json()
            assert changed["type"] == "vision_status"
            assert changed["data"]["camera"]["state"] == "LIVE"
            assert changed["data"]["revision"] == initial["data"]["revision"] + 1
            ws.close()
        assert not runtime._subscribers
