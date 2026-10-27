import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.websockets.events import router as events_router
from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.telemetry import TelemetrySample
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub


@asynccontextmanager
async def events_lifespan(app: FastAPI) -> AsyncIterator[None]:
    telemetry_hub = TelemetryHub()
    mission_hub = MissionHub()
    await telemetry_hub.publish(TelemetrySample(
        sequence=1, timestamp_utc=datetime.now(timezone.utc),
        latitude_deg=30.2672, longitude_deg=-97.7431,
        relative_altitude_m=25.0, heading_deg=90.0, ground_speed_mps=1.0,
    ))
    mission_hub.publish(MissionExecution(
        execution_id="exe", plan_id="plan", state=MissionState.RUNNING,
    ))
    app.state.telemetry_hub = telemetry_hub
    app.state.mission_hub = mission_hub
    yield


# reconnects should receive both latest snapshots before any new updates
def test_websocket_telemetry_and_mission_snapshot_and_live_state() -> None:
    app = FastAPI(lifespan=events_lifespan)
    app.include_router(events_router)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as ws:
            first = ws.receive_json()
            second = ws.receive_json()
            assert first["type"] == "telemetry_update"
            assert second["type"] == "mission_update"
            assert second["data"]["missionState"] == "RUNNING"
            assert second["data"]["executionId"] == "exe"

            def publish_pause() -> None:
                app.state.mission_hub.publish(MissionExecution(
                    execution_id="exe", plan_id="plan",
                    state=MissionState.PAUSED,
                ))

            # publish after connecting to make sure the live mission event arrives
            client.portal.call(publish_pause)
            changed = ws.receive_json()
            assert changed["type"] == "mission_update"
            assert changed["data"]["missionState"] == "PAUSED"
            # the WebSocket should remove its subscription after disconnect
            ws.close()
            for _ in range(50):
                if client.portal.call(lambda: len(app.state.mission_hub._subscribers)) == 0:
                    break
                time.sleep(0.001)
            assert client.portal.call(lambda: len(app.state.mission_hub._subscribers)) == 0

        with client.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["type"] == "telemetry_update"
            assert ws.receive_json()["data"]["missionState"] == "PAUSED"
            ws.close()
            for _ in range(50):
                if client.portal.call(lambda: len(app.state.mission_hub._subscribers)) == 0:
                    break
                time.sleep(0.001)
            assert client.portal.call(lambda: len(app.state.mission_hub._subscribers)) == 0


# clients connecting after a failure still need to see its runtimeError
def test_websocket_reconnect_reports_runtime_error() -> None:
    from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode

    app = FastAPI(lifespan=events_lifespan)
    app.include_router(events_router)
    with TestClient(app) as client:
        def publish_failure() -> None:
            app.state.mission_hub.publish(
                MissionExecution(
                    execution_id="exe", plan_id="plan",
                    state=MissionState.RUNNING,
                ),
                runtime_error=MissionExecutionError(
                    code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                    message="Unexpected disarm; guidance halted.",
                    details={"reason": "UNEXPECTED_DISARM"},
                ),
            )
        client.portal.call(publish_failure)
        with client.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["type"] == "telemetry_update"
            message = ws.receive_json()
            assert message["type"] == "mission_update"
            assert message["data"]["runtimeError"]["details"] == {
                "reason": "UNEXPECTED_DISARM"
            }
