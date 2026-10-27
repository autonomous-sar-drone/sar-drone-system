from app.api.schemas.mission_execution import (
    CommenceMissionRequest,
    mission_execution_response_from_domain,
)
from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference, Waypoint


# the frontend sends planId, even though the Python field is plan_id
def test_commence_request_accepts_frontend_plan_id() -> None:
    request = CommenceMissionRequest.model_validate(
        {
            "planId": "plan-123",
        }
    )

    assert request.plan_id == "plan-123"


# serialization should keep camelCase and the home-relative altitude label
def test_execution_response_matches_frontend_contract() -> None:
    execution = MissionExecution(
        execution_id="execution-456",
        plan_id="plan-123",
        state=MissionState.RUNNING,
        active_target=Waypoint(
            latitude_deg=30.2674,
            longitude_deg=-97.7430,
            altitude_m=25.0,
            altitude_reference=AltitudeReference.HOME_RELATIVE,
        ),
    )

    response = mission_execution_response_from_domain(execution)

    assert response.model_dump(by_alias=True, mode="json") == {
        "executionId": "execution-456",
        "planId": "plan-123",
        "missionState": "RUNNING",
        "activeTarget": {
            "latitudeDeg": 30.2674,
            "longitudeDeg": -97.7430,
            "altitudeM": 25.0,
            "altitudeReference": "HOME_RELATIVE",
        },
    }


# returning home no longer has a search target, so null is expected
def test_execution_response_allows_no_active_target() -> None:
    execution = MissionExecution(
        execution_id="execution-456",
        plan_id="plan-123",
        state=MissionState.RETURNING_HOME,
    )

    response = mission_execution_response_from_domain(execution)

    assert response.model_dump(by_alias=True, mode="json") == {
        "executionId": "execution-456",
        "planId": "plan-123",
        "missionState": "RETURNING_HOME",
        "activeTarget": None,
    }