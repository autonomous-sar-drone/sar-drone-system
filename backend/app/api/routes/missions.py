from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.mission_execution import (
    CommenceMissionRequest,
    MissionExecutionResponse,
    mission_execution_response_from_domain,
)
from app.mission.errors import MissionExecutionError
from app.mission.mission_service import MissionService


router = APIRouter(prefix="/api/missions", tags=["missions"])


def _camel_case_key(key: str) -> str:
    first, *rest = key.split("_")
    return first + "".join(part.capitalize() for part in rest)


# keep mission failures as structured 409 responses instead of generic server errors
# error detail keys use the same camelCase names as the frontend
def _http_error(error: MissionExecutionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code.value,
            "message": error.message,
            "details": {
                _camel_case_key(key): value
                for key, value in (error.details or {}).items()
            },
        },
    )


def _service(request: Request) -> MissionService:
    return request.app.state.mission_service


# these routes handle requests and responses, the service owns state and vehicle control
@router.post("/commence", response_model=MissionExecutionResponse)
async def commence(
    payload: CommenceMissionRequest, request: Request
) -> MissionExecutionResponse:
    try:
        execution = await _service(request).commence(payload.plan_id)
    except MissionExecutionError as error:
        raise _http_error(error) from error
    return mission_execution_response_from_domain(execution)


@router.post("/{execution_id}/pause", response_model=MissionExecutionResponse)
async def pause(execution_id: str, request: Request) -> MissionExecutionResponse:
    try:
        execution = await _service(request).pause(execution_id)
    except MissionExecutionError as error:
        raise _http_error(error) from error
    return mission_execution_response_from_domain(execution)


@router.post("/{execution_id}/resume", response_model=MissionExecutionResponse)
async def resume(execution_id: str, request: Request) -> MissionExecutionResponse:
    try:
        execution = await _service(request).resume(execution_id)
    except MissionExecutionError as error:
        raise _http_error(error) from error
    return mission_execution_response_from_domain(execution)


@router.post("/{execution_id}/stop", response_model=MissionExecutionResponse)
async def stop(execution_id: str, request: Request) -> MissionExecutionResponse:
    try:
        execution = await _service(request).stop(execution_id)
    except MissionExecutionError as error:
        raise _http_error(error) from error
    return mission_execution_response_from_domain(execution)


# continue is for an interrupted execution, not a new plan or execution ID
@router.post("/{execution_id}/continue", response_model=MissionExecutionResponse)
async def continue_mission(
    execution_id: str, request: Request
) -> MissionExecutionResponse:
    try:
        execution = await _service(request).continue_mission(execution_id)
    except MissionExecutionError as error:
        raise _http_error(error) from error
    return mission_execution_response_from_domain(execution)
