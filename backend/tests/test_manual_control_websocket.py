"""End-to-end ASGI HTTP/WebSocket contract tests with a fake vehicle."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes.missions import router as missions_router
from app.api.websockets.manual_control import router as manual_router
from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import FlightActivity, MissionState
from app.domain.models.route import AltitudeReference, PlanningSettings, Waypoint
from app.domain.models.telemetry import TelemetrySample
from app.geospatial.terrain_grid import TerrainGrid
from app.mission.mission_service import MissionService
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from test_manual_control_integration import ManualVehicle


def build_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        hub, events, vehicle = TelemetryHub(), MissionHub(), ManualVehicle()
        app.state.vehicle = vehicle
        heights = tuple(tuple(0.0 for _ in range(21)) for _ in range(21))
        grid = TerrainGrid("ws-test", 30.2672, -97.7431, 150, 200, 200, 10, heights)
        grid.validate()
        await hub.publish(TelemetrySample(
            1, datetime.now(timezone.utc), 30.2672, -97.7431, 18, 90, 0.0))
        service = MissionService(
            telemetry_hub=hub, mission_hub=events, vehicle=vehicle,
            default_planning_settings=PlanningSettings(search_altitude_m=18, lane_spacing_m=20),
            max_telemetry_age_s=3, max_transit_distance_m=1000,
            max_route_distance_m=10000, terrain_grid=grid,
        )
        execution = MissionExecution(
            "exec-1", "plan-1", MissionState.RUNNING,
            active_target=Waypoint(30.26725, -97.7431, 18, AltitudeReference.HOME_RELATIVE),
            flight_activity=FlightActivity.SEARCHING,
        )
        service._state = MissionState.RUNNING
        service._current_execution = execution
        app.state.mission_service = service
        app.state.telemetry_hub = hub
        app.state.mission_hub = events
        yield
        await service.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.include_router(missions_router)
    app.include_router(manual_router)
    return app


def input_frame(epoch: str, sequence: int, forward=0, up=0):
    return {
        "type": "manual_input",
        "data": {
            "executionId": "exec-1", "controlEpoch": epoch, "sequence": sequence,
            "clientSentAtMs": int(datetime.now(timezone.utc).timestamp() * 1000),
            "forward": forward, "right": 0, "up": up, "yaw": 0,
        },
    }


def test_take_ready_input_give_full_http_websocket_roundtrip():
    app = build_app()
    with TestClient(app) as client:
        resp = client.post("/api/missions/exec-1/take-controls")
        assert resp.status_code == 200
        assert resp.json()["flightActivity"] == "MANUAL_CONTROL"
        assert resp.json()["manualControlReady"] is False
        assert app.state.vehicle.flight_mode == "BRAKE"
        with client.websocket_connect("/ws/manual-control") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "manual_ready"
            epoch = ready["data"]["controlEpoch"]
            assert ready["data"]["executionId"] == "exec-1"
            assert ready["data"]["sendIntervalMs"] == 100
            assert app.state.vehicle.flight_mode == "GUIDED"
            ws.send_json(input_frame(epoch, 1))
            assert ws.receive_json() == {"type": "manual_ack", "data": {"sequence": 1, "accepted": True}}
            ws.send_json(input_frame(epoch, 2, forward=1))
            assert ws.receive_json() == {"type": "manual_ack", "data": {"sequence": 2, "accepted": True}}
            assert app.state.vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)
            returned = client.post("/api/missions/exec-1/give-controls")
            assert returned.status_code == 200
            assert returned.json()["flightActivity"] == "HOLDING"
            assert returned.json()["holdReason"] == "MANUAL_RELEASE"
            assert returned.json()["manualControlReady"] is False
            assert app.state.vehicle.flight_mode == "BRAKE"
            try:
                ws.receive_json()
                raise AssertionError("Revoked socket was not closed")
            except WebSocketDisconnect as error:
                assert error.code == 1011
        assert app.state.vehicle.flight_mode == "BRAKE"


def test_second_websocket_is_denied_and_cannot_take_authority():
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        with client.websocket_connect("/ws/manual-control") as owner:
            epoch = owner.receive_json()["data"]["controlEpoch"]
            with client.websocket_connect("/ws/manual-control") as duplicate:
                try:
                    duplicate.receive_json()
                    raise AssertionError("Duplicate socket unexpectedly received ready")
                except WebSocketDisconnect as error:
                    assert error.code == 4403
            owner.send_json(input_frame(epoch, 1))
            assert owner.receive_json()["data"]["accepted"] is True
            owner.send_json(input_frame(epoch, 2, forward=1))
            assert owner.receive_json()["data"]["accepted"] is True
            assert client.post("/api/missions/exec-1/give-controls").status_code == 200
            try:
                owner.receive_json()
                raise AssertionError("Revoked owner socket stayed open")
            except WebSocketDisconnect as error:
                assert error.code == 1011


def test_manual_channel_closes_and_brakes_on_input_timeout():
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        with client.websocket_connect("/ws/manual-control") as ws:
            epoch = ws.receive_json()["data"]["controlEpoch"]
            ws.send_json(input_frame(epoch, 1))
            assert ws.receive_json()["data"]["accepted"] is True
            ws.send_json(input_frame(epoch, 2, forward=1))
            assert ws.receive_json()["data"]["accepted"] is True
            # No more frames. Brief gaps neutralize; sustained silence BRAKEs
            # and revokes the socket.
            try:
                ws.receive_json()
                raise AssertionError("Timed-out socket unexpectedly stayed open")
            except WebSocketDisconnect as error:
                assert error.code == 1011
            assert app.state.vehicle.flight_mode == "BRAKE"
            assert app.state.mission_service.current_execution.manual_control_ready is False


def test_malformed_socket_frame_requests_neutral_and_rejects():
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        with client.websocket_connect("/ws/manual-control") as ws:
            epoch = ws.receive_json()["data"]["controlEpoch"]
            ws.send_json(input_frame(epoch, 1))
            ws.receive_json()
            ws.send_json(input_frame(epoch, 2, forward=1))
            ws.receive_json()
            ws.send_text("bad-json\n")
            assert ws.receive_json() == {"type": "manual_ack", "data": {"sequence": 0, "accepted": False}}
            assert app.state.vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
            assert client.post("/api/missions/exec-1/give-controls").status_code == 200
            try:
                ws.receive_json()
                raise AssertionError("Revoked socket stayed open")
            except WebSocketDisconnect as error:
                assert error.code == 1011


def test_socket_cannot_connect_without_take_controls():
    app = build_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/manual-control") as ws:
            try:
                ws.receive_json()
                raise AssertionError("Socket unexpectedly ready before Take Controls")
            except WebSocketDisconnect as error:
                assert error.code == 4403


def test_cross_origin_browser_socket_is_denied():
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        try:
            with client.websocket_connect(
                "/ws/manual-control", headers={"origin": "https://unrelated.example"},
            ):
                raise AssertionError("Cross-origin websocket unexpectedly accepted")
        except WebSocketDisconnect as error:
            assert error.code == 4403
        assert app.state.vehicle.flight_mode == "BRAKE"


def test_local_vite_proxy_origin_is_accepted():
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        with client.websocket_connect(
            "/ws/manual-control", headers={"origin": "http://localhost:5173", "host": "localhost:8000"},
        ) as ws:
            assert ws.receive_json()["type"] == "manual_ready"
            assert client.post("/api/missions/exec-1/give-controls").status_code == 200
            try:
                ws.receive_json()
                raise AssertionError("Revoked websocket not closed")
            except WebSocketDisconnect:
                pass


def test_brief_websocket_input_pause_stays_guided_and_accepts_fresh_release():
    """A browser send-timer stall must not trigger BRAKE or a new control epoch."""
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/missions/exec-1/take-controls").status_code == 200
        with client.websocket_connect("/ws/manual-control") as ws:
            epoch = ws.receive_json()["data"]["controlEpoch"]
            ws.send_json(input_frame(epoch, 1))
            assert ws.receive_json()["data"]["accepted"]
            ws.send_json(input_frame(epoch, 2, forward=1))
            assert ws.receive_json()["data"]["accepted"]
            time.sleep(0.8)  # longer than 450 ms soft watchdog
            assert app.state.vehicle.flight_mode == "GUIDED"
            assert app.state.mission_service.current_execution.manual_control_ready
            assert app.state.vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
            ws.send_json(input_frame(epoch, 3, forward=1))
            assert ws.receive_json()["data"]["accepted"]
            # Held W must not replay after the silent interval.
            assert app.state.vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
            ws.send_json(input_frame(epoch, 4))
            assert ws.receive_json()["data"]["accepted"]
            ws.send_json(input_frame(epoch, 5, forward=1))
            assert ws.receive_json()["data"]["accepted"]
            assert app.state.vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)
            assert sum(cmd[0] == "set_guided" for cmd in app.state.vehicle.commands) == 1
            assert client.post("/api/missions/exec-1/give-controls").status_code == 200
            assert app.state.vehicle.flight_mode == "BRAKE"
            try:
                ws.receive_json()
                raise AssertionError("Released manual socket remained open")
            except WebSocketDisconnect:
                pass
