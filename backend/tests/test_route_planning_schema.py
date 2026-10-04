from app.api.schemas.route_planning import (
    RoutePreviewRequest,
    route_preview_request_to_domain,
    route_preview_response_from_plan,
)
from app.domain.models.mission_plan import MissionPlan
from app.domain.models.route import (
    AltitudeReference,
    PlanningSettings,
    Route,
    RouteMetrics,
    Waypoint,
)
from app.domain.models.search_area import GeoPoint, SearchArea


def test_route_preview_request_converts_to_domain() -> None:
    request_data = {
        "searchArea": {
            "vertices": [
                {"latitudeDeg": 30.2672, "longitudeDeg": -97.7431},
                {"latitudeDeg": 30.2680, "longitudeDeg": -97.7431},
                {"latitudeDeg": 30.2680, "longitudeDeg": -97.7420},
            ]
        },
        "planning": {
            "searchAltitudeM": 25.0,
            "laneSpacingM": 20.0,
        },
    }

    request = RoutePreviewRequest.model_validate(request_data)

    search_area, planning_settings = route_preview_request_to_domain(request)

    assert search_area.vertices[0].latitude_deg == 30.2672
    assert search_area.vertices[0].longitude_deg == -97.7431

    assert planning_settings is not None
    assert planning_settings.search_altitude_m == 25.0
    assert planning_settings.lane_spacing_m == 20.0


def test_route_preview_request_allows_missing_planning() -> None:
    request_data = {
        "searchArea": {
            "vertices": [
                {"latitudeDeg": 30.2672, "longitudeDeg": -97.7431},
                {"latitudeDeg": 30.2680, "longitudeDeg": -97.7431},
                {"latitudeDeg": 30.2680, "longitudeDeg": -97.7420},
            ]
        }
    }

    request = RoutePreviewRequest.model_validate(request_data)

    _, planning_settings = route_preview_request_to_domain(request)

    assert planning_settings is None


def test_mission_plan_converts_to_camel_case_response() -> None:
    search_area = SearchArea(
        vertices=(
            GeoPoint(30.2672, -97.7431),
            GeoPoint(30.2680, -97.7431),
            GeoPoint(30.2680, -97.7420),
        )
    )

    planning_settings = PlanningSettings(
        search_altitude_m=25.0,
        lane_spacing_m=20.0,
    )

    route = Route(
        waypoints=(
            Waypoint(
                latitude_deg=30.2674,
                longitude_deg=-97.7430,
                altitude_m=25.0,
                altitude_reference=AltitudeReference.HOME_RELATIVE,
            ),
        )
    )

    metrics = RouteMetrics(
        distance_to_start_m=42.3,
        route_distance_m=615.7,
    )

    plan = MissionPlan(
        plan_id="test-plan-id",
        search_area=search_area,
        planning_settings=planning_settings,
        route=route,
        route_metrics=metrics,
    )

    response = route_preview_response_from_plan(plan)

    data = response.model_dump(by_alias=True, mode="json")

    assert data["planId"] == "test-plan-id"
    assert data["route"]["waypoints"][0]["latitudeDeg"] == 30.2674
    assert data["route"]["waypoints"][0]["altitudeReference"] == "HOME_RELATIVE"
    assert data["metrics"]["distanceToStartM"] == 42.3
    assert data["metrics"]["routeDistanceM"] == 615.7
    assert data["planningUsed"]["searchAltitudeM"] == 25.0
    assert data["planningUsed"]["laneSpacingM"] == 20.0