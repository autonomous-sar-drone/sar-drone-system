from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.missions import router as missions_router
from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference, Waypoint
from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode


# fake mission actions let these tests check the API contract without a drone
class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.execution = MissionExecution(
            execution_id="execution-123", plan_id="plan-123",
            state=MissionState.RUNNING,
            active_target=Waypoint(
                latitude_deg=30.2674, longitude_deg=-97.7430,
                altitude_m=25.0,
                altitude_reference=AltitudeReference.HOME_RELATIVE,
            ),
        )

    async def commence(self, plan_id: str) -> MissionExecution:
        self.calls.append(("commence", plan_id))
        if plan_id != "plan-123":
            raise MissionExecutionError(
                MissionExecutionErrorCode.PLAN_NOT_CURRENT,
                "The requested mission plan is not current.",
                {"requested_plan_id": plan_id, "current_plan_id": "plan-123"},
            )
        return self.execution

    async def pause(self, execution_id: str) -> MissionExecution:
        self.calls.append(("pause", execution_id))
        self.execution.state = MissionState.PAUSED
        return self.execution

    async def resume(self, execution_id: str) -> MissionExecution:
        self.calls.append(("resume", execution_id))
        self.execution.state = MissionState.RUNNING
        return self.execution

    async def stop(self, execution_id: str) -> MissionExecution:
        self.calls.append(("stop", execution_id))
        self.execution.state = MissionState.RETURNING_HOME
        self.execution.active_target = None
        return self.execution

    async def continue_mission(self, execution_id: str) -> MissionExecution:
        self.calls.append(("continue", execution_id))
        self.execution.state = MissionState.RUNNING
        return self.execution


def make_app(service: FakeService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.mission_service = service
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(missions_router)
    return app


# all five HTTP actions should return the same frontend-facing field names
def test_five_actions_return_frontend_contract() -> None:
    service = FakeService()
    with TestClient(make_app(service)) as client:
        started = client.post("/api/missions/commence", json={"planId": "plan-123"})
        paused = client.post("/api/missions/execution-123/pause")
        resumed = client.post("/api/missions/execution-123/resume")
        stopped = client.post("/api/missions/execution-123/stop")
        continued = client.post("/api/missions/execution-123/continue")

    assert started.status_code == 200
    assert started.json() == {
        "executionId": "execution-123", "planId": "plan-123",
        "missionState": "RUNNING", "activeTarget": {
            "latitudeDeg": 30.2674, "longitudeDeg": -97.7430,
            "altitudeM": 25.0, "altitudeReference": "HOME_RELATIVE",
        },
    }
    assert paused.json()["missionState"] == "PAUSED"
    assert resumed.json()["missionState"] == "RUNNING"
    assert stopped.json()["missionState"] == "RETURNING_HOME"
    assert stopped.json()["activeTarget"] is None
    assert continued.json()["missionState"] == "RUNNING"
    assert service.calls == [
        ("commence", "plan-123"),
        ("pause", "execution-123"),
        ("resume", "execution-123"),
        ("stop", "execution-123"),
        ("continue", "execution-123"),
    ]


# rejected actions use 409 with machine-readable code and details
def test_invalid_plan_returns_structured_error() -> None:
    with TestClient(make_app(FakeService())) as client:
        response = client.post("/api/missions/commence", json={"planId": "bad"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "PLAN_NOT_CURRENT",
            "message": "The requested mission plan is not current.",
            "details": {"requestedPlanId": "bad", "currentPlanId": "plan-123"},
        }
    }


# missing request fields are validation errors before the service runs
def test_missing_commence_plan_id_is_422() -> None:
    with TestClient(make_app(FakeService())) as client:
        response = client.post("/api/missions/commence", json={})
    assert response.status_code == 422
