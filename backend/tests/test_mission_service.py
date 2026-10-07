from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.mission.errors import MissionPlanningError, MissionPlanningErrorCode
from app.mission.mission_service import MissionService
from app.runtime.telemetry_hub import TelemetryHub


def make_search_area() -> SearchArea:
    return SearchArea(
        vertices=(
            GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7431),
            GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7426),
            GeoPoint(latitude_deg=30.2676, longitude_deg=-97.7426),
            GeoPoint(latitude_deg=30.2676, longitude_deg=-97.7431),
        )
    )


def make_invalid_search_area() -> SearchArea:
    return SearchArea(
        vertices=(
            GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7431),
            GeoPoint(latitude_deg=30.2673, longitude_deg=-97.7430),
        )
    )


def make_sample(
    latitude_deg: float = 30.2672,
    longitude_deg: float = -97.7431,
    age_s: float = 0.0,
) -> TelemetrySample:
    return TelemetrySample(
        sequence=1,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        relative_altitude_m=25.0,
        heading_deg=90.0,
        ground_speed_mps=4.0,
    )


def make_service(
    hub: TelemetryHub,
    max_telemetry_age_s: float = 3.0,
    max_transit_distance_m: float = 2000.0,
    max_route_distance_m: float = 10000.0,
) -> MissionService:
    return MissionService(
        telemetry_hub=hub,
        default_planning_settings=PlanningSettings(
            search_altitude_m=25.0,
            lane_spacing_m=20.0,
        ),
        max_telemetry_age_s=max_telemetry_age_s,
        max_transit_distance_m=max_transit_distance_m,
        max_route_distance_m=max_route_distance_m,
    )


@pytest.mark.asyncio
async def test_preview_route_uses_defaults_and_stores_proposed_plan() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())
    service = make_service(hub)

    plan = await service.preview_route(make_search_area())

    assert plan.plan_id
    assert plan.planning_settings.search_altitude_m == 25.0
    assert plan.planning_settings.lane_spacing_m == 20.0
    assert len(plan.route.waypoints) >= 2
    assert service.state == MissionState.PLANNING
    assert service.proposed_plan == plan


@pytest.mark.asyncio
async def test_preview_route_uses_requested_planning_settings() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())
    service = make_service(hub)

    requested = PlanningSettings(
        search_altitude_m=30.0,
        lane_spacing_m=15.0,
    )

    plan = await service.preview_route(
        search_area=make_search_area(),
        planning_settings=requested,
    )

    assert plan.planning_settings == requested
    assert all(
        waypoint.altitude_m == 30.0
        for waypoint in plan.route.waypoints
    )


@pytest.mark.asyncio
async def test_preview_route_rejects_invalid_search_area() -> None:
    hub = TelemetryHub()
    service = make_service(hub)

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_invalid_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.INVALID_SEARCH_AREA
    assert error.details == {"reason": "TOO_FEW_VERTICES"}
    assert service.state == MissionState.PLANNING
    assert service.proposed_plan is None


@pytest.mark.asyncio
async def test_preview_route_requires_vehicle_telemetry() -> None:
    hub = TelemetryHub()
    service = make_service(hub)

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE
    assert error.details == {"reason": "NO_TELEMETRY"}


@pytest.mark.asyncio
async def test_preview_route_rejects_stale_telemetry() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample(age_s=10.0))

    service = make_service(
        hub,
        max_telemetry_age_s=3.0,
    )

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE
    assert error.details is not None
    assert error.details["reason"] == "STALE_TELEMETRY"

    telemetry_age_s = error.details["telemetry_age_seconds"]
    assert isinstance(telemetry_age_s, float)
    assert telemetry_age_s > 3.0

    assert error.details["max_telemetry_age_seconds"] == 3.0


@pytest.mark.asyncio
async def test_preview_route_rejects_search_area_too_far_away() -> None:
    hub = TelemetryHub()

    await hub.publish(
        make_sample(
            latitude_deg=30.30,
            longitude_deg=-97.80,
        )
    )

    service = make_service(
        hub,
        max_transit_distance_m=100.0,
    )

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.SEARCH_AREA_OUT_OF_RANGE
    assert error.details is not None

    distance_to_start_m = error.details["distance_to_start_m"]
    assert isinstance(distance_to_start_m, float)
    assert distance_to_start_m > 100.0

    assert error.details["max_transit_distance_m"] == 100.0


@pytest.mark.asyncio
async def test_preview_route_rejects_route_over_configured_limit() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())

    service = make_service(
        hub,
        max_route_distance_m=1.0,
    )

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.ROUTE_TOO_LONG
    assert error.details is not None

    route_distance_m = error.details["route_distance_m"]
    assert isinstance(route_distance_m, float)
    assert route_distance_m > 1.0

    assert error.details["max_route_distance_m"] == 1.0


@pytest.mark.asyncio
async def test_preview_route_rejects_action_during_running_mission() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())

    service = make_service(hub)
    service._state = MissionState.RUNNING

    with pytest.raises(MissionPlanningError) as error_info:
        await service.preview_route(make_search_area())

    error = error_info.value

    assert error.code == MissionPlanningErrorCode.MISSION_ACTION_NOT_ALLOWED
    assert error.details == {"current_state": "RUNNING"}


@pytest.mark.asyncio
async def test_new_preview_replaces_previous_proposed_plan() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())

    service = make_service(hub)

    first_plan = await service.preview_route(make_search_area())
    second_plan = await service.preview_route(make_search_area())

    assert first_plan.plan_id != second_plan.plan_id
    assert service.proposed_plan == second_plan


@pytest.mark.asyncio
async def test_failed_new_preview_invalidates_previous_plan() -> None:
    hub = TelemetryHub()
    await hub.publish(make_sample())

    service = make_service(hub)

    await service.preview_route(make_search_area())
    assert service.proposed_plan is not None

    with pytest.raises(MissionPlanningError):
        await service.preview_route(make_invalid_search_area())

    assert service.proposed_plan is None