from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.route_planning import (
    RoutePreviewErrorResponse,
    RoutePreviewRequest,
    RoutePreviewResponse,
    route_preview_request_to_domain,
    route_preview_response_from_plan,
)
from app.mission.errors import MissionPlanningError, MissionPlanningErrorCode
from app.mission.mission_service import MissionService


router = APIRouter(prefix="/api/routes", tags=["routes"])


def _camel_case_key(key: str) -> str:
    first, *rest = key.split("_")
    return first + "".join(part.capitalize() for part in rest)


def _error_details_for_api(
    details: dict[str, object] | None,
) -> dict[str, object] | None:
    if details is None:
        return None

    return {
        _camel_case_key(key): value
        for key, value in details.items()
    }


ERROR_STATUS_CODES = {
    MissionPlanningErrorCode.INVALID_SEARCH_AREA: status.HTTP_422_UNPROCESSABLE_ENTITY,
    MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE: status.HTTP_409_CONFLICT,
    MissionPlanningErrorCode.SEARCH_AREA_OUT_OF_RANGE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    MissionPlanningErrorCode.ROUTE_TOO_LONG: status.HTTP_422_UNPROCESSABLE_ENTITY,
    MissionPlanningErrorCode.MISSION_ACTION_NOT_ALLOWED: status.HTTP_409_CONFLICT,
}


@router.post(
    "/preview",
    response_model=RoutePreviewResponse,
    response_model_by_alias=True,
    responses={
        409: {"model": RoutePreviewErrorResponse},
        422: {"model": RoutePreviewErrorResponse},
    },
)
async def preview_route(
    request: RoutePreviewRequest,
    http_request: Request,
) -> RoutePreviewResponse:
    search_area, planning_settings = route_preview_request_to_domain(request)
    mission_service: MissionService = http_request.app.state.mission_service

    try:
        plan = await mission_service.preview_route(
            search_area=search_area,
            planning_settings=planning_settings,
        )
    except MissionPlanningError as error:
        raise HTTPException(
            status_code=ERROR_STATUS_CODES[error.code],
            detail={
                "code": error.code.value,
                "message": error.message,
                "details": _error_details_for_api(error.details),
            },
        ) from error

    return route_preview_response_from_plan(plan)