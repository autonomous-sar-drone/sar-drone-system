from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes.vision import router as vision_router, mjpeg_parts
from app.vision.vision_runtime import VisionRuntime
from test_vision_runtime import FakeCameraReceiver


@asynccontextmanager
async def runtime_lifespan(app: FastAPI) -> AsyncIterator[None]:
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera)
    app.state.vision_runtime = runtime
    app.state.test_camera = camera
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


def test_status_route_contract_and_missing_source_503():
    app = FastAPI(lifespan=runtime_lifespan)
    app.include_router(vision_router)
    with TestClient(app) as client:
        response = client.get("/api/vision/status")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {
            "sessionId", "revision", "timestamp", "camera", "detector",
            "cameraOutageSeconds", "automaticRtlTriggered",
        }
        assert body["camera"] == {"state": "STARTING", "lastFrameAt": None}
        assert body["detector"] == {"state": "UNAVAILABLE", "lastInferenceAt": None}
        assert client.get("/api/camera/stream.mjpg").status_code == 503


@pytest.mark.asyncio
async def test_multi_client_stream_same_jpeg_and_ends_on_camera_loss():
    camera = FakeCameraReceiver()
    runtime = VisionRuntime(camera)
    await runtime.start()
    try:
        camera.push()
        await runtime.poll_once()
        stream_a = mjpeg_parts(runtime)
        stream_b = mjpeg_parts(runtime)
        first = await anext(stream_a)
        second = await anext(stream_b)
        assert first == second
        assert first.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
        assert b"Content-Length: " in first
        assert first.endswith(camera.frame.jpeg_bytes + b"\r\n")
        camera.arrived_at -= 6
        await runtime.poll_once()
        with pytest.raises(StopAsyncIteration):
            await anext(stream_a)
        await stream_b.aclose()
    finally:
        await runtime.stop()
