from pydantic import BaseModel, Field

from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference


class CommenceMissionRequest(BaseModel):
    plan_id: str = Field(validation_alias="planId")


class ActiveTargetResponse(BaseModel):
    latitude_deg: float = Field(serialization_alias="latitudeDeg")
    longitude_deg: float = Field(serialization_alias="longitudeDeg")
    altitude_m: float = Field(serialization_alias="altitudeM")
    altitude_reference: AltitudeReference = Field(
        serialization_alias="altitudeReference"
    )


class MissionExecutionResponse(BaseModel):
    execution_id: str = Field(serialization_alias="executionId")
    plan_id: str = Field(serialization_alias="planId")
    mission_state: MissionState = Field(serialization_alias="missionState")
    active_target: ActiveTargetResponse | None = Field(
        serialization_alias="activeTarget"
    )


def mission_execution_response_from_domain(
    execution: MissionExecution,
) -> MissionExecutionResponse:
    active_target = None

    if execution.active_target is not None:
        active_target = ActiveTargetResponse(
            latitude_deg=execution.active_target.latitude_deg,
            longitude_deg=execution.active_target.longitude_deg,
            altitude_m=execution.active_target.altitude_m,
            altitude_reference=execution.active_target.altitude_reference,
        )

    return MissionExecutionResponse(
        execution_id=execution.execution_id,
        plan_id=execution.plan_id,
        mission_state=execution.state,
        active_target=active_target,
    )