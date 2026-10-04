from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models.mission_plan import MissionPlan
from app.domain.models.route import AltitudeReference, PlanningSettings
from app.domain.models.search_area import GeoPoint, SearchArea


# Request schemas

class GeoPointRequest(BaseModel):
    latitude_deg: float = Field(validation_alias="latitudeDeg")
    longitude_deg: float = Field(validation_alias="longitudeDeg")


class SearchAreaRequest(BaseModel):
    vertices: list[GeoPointRequest]


class PlanningRequest(BaseModel):
    search_altitude_m: float = Field(validation_alias="searchAltitudeM")
    lane_spacing_m: float = Field(validation_alias="laneSpacingM")


class RoutePreviewRequest(BaseModel):
    search_area: SearchAreaRequest = Field(validation_alias="searchArea")
    planning: PlanningRequest | None = None


# Response schemas

class WaypointResponse(BaseModel):
    latitude_deg: float = Field(serialization_alias="latitudeDeg")
    longitude_deg: float = Field(serialization_alias="longitudeDeg")
    altitude_m: float = Field(serialization_alias="altitudeM")
    altitude_reference: AltitudeReference = Field(serialization_alias="altitudeReference")


class RouteResponse(BaseModel):
    waypoints: list[WaypointResponse]


class RouteMetricsResponse(BaseModel):
    distance_to_start_m: float = Field(serialization_alias="distanceToStartM")
    route_distance_m: float = Field(serialization_alias="routeDistanceM")


class PlanningUsedResponse(BaseModel):
    search_altitude_m: float = Field(serialization_alias="searchAltitudeM")
    lane_spacing_m: float = Field(serialization_alias="laneSpacingM")


class RoutePreviewResponse(BaseModel):
    plan_id: str = Field(serialization_alias="planId")
    route: RouteResponse
    metrics: RouteMetricsResponse
    planning_used: PlanningUsedResponse = Field(serialization_alias="planningUsed")


# Error schemas

RoutePreviewErrorCode = Literal[
    "INVALID_SEARCH_AREA",
    "VEHICLE_POSITION_UNAVAILABLE",
    "SEARCH_AREA_OUT_OF_RANGE",
    "ROUTE_TOO_LONG",
    "MISSION_ACTION_NOT_ALLOWED",
]


class RoutePreviewErrorDetail(BaseModel):
    code: RoutePreviewErrorCode
    message: str
    details: dict[str, object] | None = None


class RoutePreviewErrorResponse(BaseModel):
    detail: RoutePreviewErrorDetail


# Request -> domain conversion

def search_area_from_request(request: SearchAreaRequest) -> SearchArea:
    return SearchArea(
            vertices=tuple(
            GeoPoint(latitude_deg=vertex.latitude_deg, longitude_deg=vertex.longitude_deg)
            for vertex in request.vertices
        )
    )


def planning_settings_from_request(request: PlanningRequest | None) -> PlanningSettings | None:
    if request is None:
        return None

    return PlanningSettings(
        search_altitude_m=request.search_altitude_m,
        lane_spacing_m=request.lane_spacing_m,
    )


def route_preview_request_to_domain(request: RoutePreviewRequest) -> tuple[SearchArea, PlanningSettings | None]:
    return (
        search_area_from_request(request.search_area),
        planning_settings_from_request(request.planning),
    )


# Domain to response conversion

def route_preview_response_from_plan(plan: MissionPlan) -> RoutePreviewResponse:
    return RoutePreviewResponse(
        plan_id=plan.plan_id,
        route=RouteResponse(
            waypoints=[
                WaypointResponse(
                    latitude_deg=waypoint.latitude_deg,
                    longitude_deg=waypoint.longitude_deg,
                    altitude_m=waypoint.altitude_m,
                    altitude_reference=waypoint.altitude_reference,
                )
                for waypoint in plan.route.waypoints
            ]
        ),
        metrics=RouteMetricsResponse(
            distance_to_start_m=plan.route_metrics.distance_to_start_m,
            route_distance_m=plan.route_metrics.route_distance_m,
        ),
        planning_used=PlanningUsedResponse(
            search_altitude_m=plan.planning_settings.search_altitude_m,
            lane_spacing_m=plan.planning_settings.lane_spacing_m,
        ),
    )