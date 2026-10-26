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

    app = FastAPI(lifespan=bootstrap.lifespan)
    from fastapi.testclient import TestClient

    with TestClient(app):
        assert calls == ["vehicle_start"]
        assert app.state.mission_service._vehicle is app.state.vehicle
        assert app.state.vehicle.telemetry_hub is app.state.telemetry_hub
        assert app.state.mission_service._mission_hub is app.state.mission_hub

    # shutdown order matters because the receiver owns the MAVLink connection
    assert calls == ["vehicle_start", "vehicle_stop", "connection_close"]
