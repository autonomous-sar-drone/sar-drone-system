import asyncio

import pytest

from app.domain.models.route import PlanningSettings
from app.geospatial.terrain_grid import TerrainGrid
from app.mission.mission_service import MissionService
from app.mission.errors import MissionExecutionError
from app.mission.rtl_safety import safe_home_relative_rtl_altitude
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError
from test_mission_execution import FakeVehicle, make_sample


def terrain(maximum_m=24.0):
    grid = TerrainGrid(
        scenario_id="test", origin_latitude_deg=30.0,
        origin_longitude_deg=-97.0, origin_ground_elevation_dem_m=360.0,
        width_m=8, height_m=8, spacing_m=4,
        heights=((0, 0, 0), (0, 0, 0), (0, 0, maximum_m)),
    )
    grid.validate()
    return grid


def test_normal_scenario_keeps_verified_50_m_fallback():
    assert safe_home_relative_rtl_altitude(terrain(21.85)) == 50


def test_higher_ground_increases_home_relative_altitude():
    assert safe_home_relative_rtl_altitude(terrain(40)) == 65
    assert safe_home_relative_rtl_altitude(terrain(51)) == 80


def test_custom_floor_is_never_reduced():
    assert safe_home_relative_rtl_altitude(terrain(0), minimum_altitude_m=57) == 60


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_safety_settings_are_rejected(value):
    with pytest.raises(ValueError):
        safe_home_relative_rtl_altitude(terrain(), required_clearance_m=value)


class RtlVehicle(FakeVehicle):
    def __init__(self):
        super().__init__()
        self.rtl_configurations = []
        self.fail_configuration = False

    async def configure_safe_rtl(self, altitude_m):
        if self.fail_configuration:
            raise VehicleCommandError("Failed to verify RTL parameters")
        assert not self.armed
        self.rtl_configurations.append(altitude_m)


def service_with_rtl(vehicle):
    return MissionService(
        telemetry_hub=TelemetryHub(),
        default_planning_settings=PlanningSettings(search_altitude_m=18, lane_spacing_m=20),
        max_telemetry_age_s=3, max_transit_distance_m=2000,
        max_route_distance_m=10000, vehicle=vehicle,
        takeoff_timeout_s=0.3, rtl_home_altitude_m=50,
    )


@pytest.mark.asyncio
async def test_safe_rtl_is_configured_before_arming_and_takeoff():
    vehicle = RtlVehicle()
    service = service_with_rtl(vehicle)
    task = asyncio.create_task(service._prepare_vehicle_for_search(vehicle, 18))
    try:
        async def wait_for_takeoff():
            while not vehicle.takeoff_calls:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_takeoff(), timeout=0.3)
        assert vehicle.rtl_configurations == [50]
        assert vehicle.arm_calls == 1
        await service._telemetry_hub.publish(make_sample(1, relative_altitude_m=18))
        await task
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_configuration_failure_blocks_arming():
    vehicle = RtlVehicle()
    vehicle.fail_configuration = True
    service = service_with_rtl(vehicle)
    try:
        with pytest.raises(MissionExecutionError) as exc_info:
            await service._prepare_vehicle_for_search(vehicle, 18)
        assert "Failed to verify RTL parameters" in exc_info.value.details["reason"]
        assert vehicle.arm_calls == 0
        assert vehicle.takeoff_calls == []
    finally:
        await service.shutdown()
