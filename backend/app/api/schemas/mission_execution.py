from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference
from app.runtime.mission_hub import MissionSnapshot


# API fields use camelCase even though the Python code uses snake_case
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


# the frontend only needs the current mission state and target, not the full history
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

class RuntimeErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, object]


# WebSocket updates also carry timestamps and guidance errors, unlike HTTP replies
class MissionUpdateData(MissionExecutionResponse):
    runtime_error: RuntimeErrorResponse | None = Field(
        default=None, serialization_alias="runtimeError"
    )
    timestamp: datetime


class MissionUpdateEvent(BaseModel):
    type: str = "mission_update"
    data: MissionUpdateData


# convert the stored snapshot into the mission_update shape used by /ws/events
def mission_update_event_from_snapshot(snapshot: MissionSnapshot) -> MissionUpdateEvent:
    target = snapshot.active_target
    active_target = None
    if target is not None:
        active_target = ActiveTargetResponse(
            latitude_deg=target.latitude_deg,
            longitude_deg=target.longitude_deg,
            altitude_m=target.altitude_m,
            altitude_reference=target.altitude_reference,
        )
    return MissionUpdateEvent(
        data=MissionUpdateData(
            timestamp=snapshot.timestamp_utc,
            execution_id=snapshot.execution_id,
            plan_id=snapshot.plan_id,
            mission_state=snapshot.state,
            active_target=active_target,
            runtime_error=RuntimeErrorResponse(
                code=snapshot.runtime_error.code,
                message=snapshot.runtime_error.message,
                details=snapshot.runtime_error.details,
            ) if snapshot.runtime_error is not None else None,
        )
    )
