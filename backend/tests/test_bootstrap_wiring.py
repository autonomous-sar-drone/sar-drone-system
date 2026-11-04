from fastapi import FastAPI
import pytest


def test_bootstrap_uses_vehicle_receiver_and_stops_it(monkeypatch) -> None:
    # bootstrap imports the real adapter, so this test needs pymavlink installed
    pytest.importorskip("pymavlink")

    from app import bootstrap

    calls: list[str] = []

    class FakeConnection:
        def close(self) -> None:
            calls.append("connection_close")

    class FakeMavlinkVehicle:
        def __init__(self, connection, telemetry_hub) -> None:
            self.connection = connection
            self.telemetry_hub = telemetry_hub

        async def start(self) -> None:
            calls.append("vehicle_start")

        async def stop(self) -> None:
            calls.append("vehicle_stop")
            self.connection.close()

        def get_status(self):
            return None

    # fake the connection so the lifespan test does not need a running SITL
    monkeypatch.setattr(
        bootstrap, "create_mavlink_connection", lambda endpoint: FakeConnection()
    )
    monkeypatch.setattr(bootstrap, "MavlinkVehicle", FakeMavlinkVehicle)

    class FakeCameraReceiver:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def start(self):
            calls.append("camera_start")

        def stop(self):
            calls.append("camera_stop")

    monkeypatch.setattr(bootstrap, "CameraReceiver", FakeCameraReceiver)

    class FakeVisionRuntime:
        def __init__(self, receiver, **kwargs):
            assert isinstance(receiver, FakeCameraReceiver)
            assert isinstance(kwargs["detector"], bootstrap.YoloPersonTracker)
            assert kwargs["max_inference_fps"] == bootstrap.VISION_INFERENCE_MAX_FPS
            self.session_id = "vision-session-test-bootstrap"

        def subscribe_detections(self):
            import asyncio
            calls.append("candidate_subscribe")
            self._candidate_queue = asyncio.Queue(maxsize=16)
            return self._candidate_queue

        def unsubscribe_detections(self, queue):
            assert queue is self._candidate_queue
            calls.append("candidate_unsubscribe")

        async def start(self):
            calls.append("vision_start")

        async def stop(self):
            calls.append("vision_stop")

    monkeypatch.setattr(bootstrap, "VisionRuntime", FakeVisionRuntime)
    monkeypatch.setattr(bootstrap, "VISION_DETECTOR_ENABLED", True)

    app = FastAPI(lifespan=bootstrap.lifespan)
    from fastapi.testclient import TestClient

    with TestClient(app):
        assert calls == ["vehicle_start", "camera_start", "candidate_subscribe", "vision_start"]
        assert app.state.candidate_store.session_id == app.state.vision_runtime.session_id
        assert app.state.candidate_store.collection() == (0, [])
        assert app.state.mission_service._vehicle is app.state.vehicle
        assert app.state.vehicle.telemetry_hub is app.state.telemetry_hub
        assert app.state.mission_service._mission_hub is app.state.mission_hub

    # shutdown order matters because the receiver owns the MAVLink connection
    assert calls == ["vehicle_start", "camera_start", "candidate_subscribe", "vision_start", "vision_stop", "candidate_unsubscribe", "camera_stop", "vehicle_stop", "connection_close"]
