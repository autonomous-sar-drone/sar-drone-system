import asyncio
from datetime import datetime, timezone

import pytest

from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.mission.mission_service import MissionService
from app.runtime.telemetry_hub import TelemetryHub
from test_mission_execution import FakeVehicle
from test_terrain_navigation import terrain_grid


def sample(sequence, latitude, longitude, altitude=0.0):
    return TelemetrySample(
        sequence=sequence, timestamp_utc=datetime.now(timezone.utc),
        latitude_deg=latitude, longitude_deg=longitude,
        relative_altitude_m=altitude, heading_deg=0, ground_speed_mps=0,
    )


async def wait_until(predicate):
    async def spin():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(spin(), timeout=1.0)


async def create_running():
    terrain = terrain_grid()
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = MissionService(
        telemetry_hub=hub, vehicle=vehicle,
        default_planning_settings=PlanningSettings(search_altitude_m=18,
                                                    lane_spacing_m=10),
        max_telemetry_age_s=3, max_transit_distance_m=2000,
        max_route_distance_m=10000, takeoff_timeout_s=0.5,
        terrain_grid=terrain, terrain_step_m=5,
    )
    origin = terrain.local_to_geo(0, 0)
    await hub.publish(sample(1, *origin))
    area = SearchArea(vertices=tuple(GeoPoint(*terrain.local_to_geo(n, w)) for n, w in (
        (18, -10), (35, -10), (35, 10), (18, 10),
    )))
    plan = await service.preview_route(area)
    task = asyncio.create_task(service.commence(plan.plan_id))
    await wait_until(lambda: bool(vehicle.takeoff_calls))
    await hub.publish(sample(2, *origin, vehicle.takeoff_calls[-1]))
    execution = await task
    return terrain, hub, vehicle, service, plan, execution


@pytest.mark.asyncio
async def test_terrain_search_keeps_visible_waypoints():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        assert len(service._navigation_commands) > len(plan.route.waypoints)
        assert execution.active_target == plan.route.waypoints[0]
        first_micro = service._navigation_commands[0].target
        assert vehicle.go_to_calls[-1] != first_micro
        assert execution.completed_targets == []
        for i, command in enumerate(c for c in service._navigation_commands
                                    if c.completes_visible):
            await hub.publish(sample(i + 3, command.target.latitude_deg,
                                     command.target.longitude_deg, command.target.altitude_m))
            await wait_until(lambda: len(execution.completed_targets) > i)
        assert execution.completed_targets == list(plan.route.waypoints)
        assert execution.state == MissionState.RETURNING_HOME
        assert vehicle.rtl_calls == 1
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_pause_resume_replans_from_actual_location():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.pause(execution.execution_id)
        moved = terrain.local_to_geo(3, 4)
        await hub.publish(sample(3, *moved, 19))
        await service.resume(execution.execution_id)
        assert execution.state == MissionState.RUNNING
        assert len(service._recovery_commands) > 0
        assert vehicle.go_to_calls[-1] == service._recovery_commands[0]
        assert execution.active_target == plan.route.waypoints[0]
        assert execution.completed_targets == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_landing_continue_replans_to_checkpoint_then_search():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        original_target = service._navigation_target
        assert original_target is not None
        interruption = terrain.local_to_geo(8, 6)
        await hub.publish(sample(3, *interruption, 20))
        await service.stop(execution.execution_id)
        assert execution.resume_checkpoint.telemetry.latitude_deg == interruption[0]
        assert service._navigation_target == original_target
        vehicle.armed = False
        await hub.publish(sample(4, *terrain.local_to_geo(0, 0), 0.0))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)
        task = asyncio.create_task(service.continue_mission(execution.execution_id))
        await wait_until(lambda: len(vehicle.takeoff_calls) == 2)
        await hub.publish(sample(5, *terrain.local_to_geo(0, 0), vehicle.takeoff_calls[-1]))
        await task
        assert service._recovery_commands
        assert vehicle.go_to_calls[-1] != service._recovery_commands[0]
        assert execution.completed_targets == []
        ends = [service._recovery_commands[index]
                for index in service._recovery_segment_ends]
        for index, target in enumerate(ends):
            await hub.publish(sample(index + 6, target.latitude_deg,
                                     target.longitude_deg, target.altitude_m))
            if index == len(ends) - 1:
                await wait_until(lambda: not service._recovery_commands)
            else:
                await wait_until(lambda: service._recovery_index >
                                 service._recovery_segment_ends[index])
        assert service._resuming_to_checkpoint is False
        assert service._navigation_index >= 0
        assert execution.active_target == plan.route.waypoints[0]
        assert execution.active_target == plan.route.waypoints[0]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_again_during_recovery_retains_original_anchor():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        interrupted = terrain.local_to_geo(8, 6)
        await hub.publish(sample(3, *interrupted, 20))
        await service.stop(execution.execution_id)
        vehicle.armed = False
        await hub.publish(sample(4, *terrain.local_to_geo(0, 0), 0.0))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)
        task = asyncio.create_task(service.continue_mission(execution.execution_id))
        await wait_until(lambda: len(vehicle.takeoff_calls) == 2)
        await hub.publish(sample(5, *terrain.local_to_geo(0, 0), vehicle.takeoff_calls[-1]))
        await task
        anchor = execution.resume_checkpoint
        await hub.publish(sample(6, *terrain.local_to_geo(4, 2), 19))
        await service.stop(execution.execution_id)
        assert execution.resume_checkpoint == anchor
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_wrong_terrain_origin_blocks_before_arming():
    terrain = terrain_grid()
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = MissionService(
        telemetry_hub=hub, vehicle=vehicle,
        default_planning_settings=PlanningSettings(18, 10),
        max_telemetry_age_s=3, max_transit_distance_m=20000,
        max_route_distance_m=10000, terrain_grid=terrain,
    )
    origin = terrain.local_to_geo(25, 0)
    await hub.publish(sample(1, *origin))
    area = SearchArea(vertices=tuple(GeoPoint(*terrain.local_to_geo(n, w)) for n, w in (
        (18, -10), (35, -10), (35, 10), (18, 10),
    )))
    plan = await service.preview_route(area)
    with pytest.raises(Exception, match="Terrain navigation"):
        await service.commence(plan.plan_id)
    assert service.state == MissionState.PLANNING
    assert not vehicle.takeoff_calls
    assert vehicle.arm_calls == 0
    await service.shutdown()


@pytest.mark.asyncio
async def test_micro_points_advance_without_vertical_dwell_but_visible_goal_does_not():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        micro = service._navigation_target
        await hub.publish(sample(3, micro.latitude_deg, micro.longitude_deg,
                                 micro.altitude_m - 1.0))
        await wait_until(lambda: service._navigation_index > 0)
        assert execution.completed_targets == []
        goal = next(c.target for c in service._navigation_commands if c.completes_visible)
        await hub.publish(sample(4, goal.latitude_deg, goal.longitude_deg,
                                 goal.altitude_m - 1.0))
        await asyncio.sleep(0.02)
        assert execution.completed_targets == []
        await hub.publish(sample(5, goal.latitude_deg, goal.longitude_deg,
                                 goal.altitude_m))
        await wait_until(lambda: len(execution.completed_targets) == 1)
        assert execution.completed_targets[0] == plan.route.waypoints[0]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_low_terrain_clearance_brakes_and_reports_error():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        elevated = terrain.local_to_geo(25, 0)
        await hub.publish(sample(3, *elevated, 10.0))
        await wait_until(lambda: service.background_execution_error is not None)
        assert service.background_execution_error.details["reason"] == "TERRAIN_CLEARANCE_LOW"
        assert vehicle.hold_calls == 1
        assert execution.state == MissionState.PAUSED
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_continue_rejects_unknown_checkpoint_before_arming():
    from app.domain.models.mission_execution import ResumeCheckpoint
    from app.mission.errors import MissionExecutionError

    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.stop(execution.execution_id)
        vehicle.armed = False
        await hub.publish(sample(4, *terrain.local_to_geo(0, 0)))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)
        outside = terrain.local_to_geo(150, 0)
        execution.resume_checkpoint = ResumeCheckpoint(
            telemetry=sample(5, *outside, 18), active_target=service._navigation_target,
        )
        old_arms = vehicle.arm_calls
        with pytest.raises(MissionExecutionError, match="Terrain navigation"):
            await service.continue_mission(execution.execution_id)
        assert vehicle.arm_calls == old_arms
        assert execution.state == MissionState.INTERRUPTED
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_straight_leg_uses_moving_targets_and_no_micro_stops():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        start = service._terrain_launch_origin
        assert start is not None
        initial = vehicle.go_to_calls[-1]
        n0, w0 = terrain.geo_to_local(start.latitude_deg, start.longitude_deg)
        nt, wt = terrain.geo_to_local(initial.latitude_deg, initial.longitude_deg)
        assert ((nt - n0) ** 2 + (wt - w0) ** 2) ** .5 == pytest.approx(8, abs=0.05)
        for i, command in enumerate(service._navigation_commands[:3]):
            await hub.publish(sample(3 + i, command.target.latitude_deg,
                                     command.target.longitude_deg, command.target.altitude_m))
            await wait_until(lambda: service._navigation_index >= i + 1)
            assert vehicle.hold_calls == 0
            assert execution.completed_targets == []
        await hub.publish(sample(10, *terrain.local_to_geo(0, 9), 19))
        await wait_until(lambda: service.background_execution_error is not None)
        assert vehicle.hold_calls == 1
        assert execution.state == MissionState.PAUSED
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_resume_still_finishes_a_visible_leg():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.pause(execution.execution_id)
        await hub.publish(sample(3, *terrain.local_to_geo(2, 1), 20))
        await service.resume(execution.execution_id)
        recovery_end = service._recovery_commands[-1]
        await hub.publish(sample(4, recovery_end.latitude_deg,
                                 recovery_end.longitude_deg, recovery_end.altitude_m))
        await wait_until(lambda: not service._recovery_commands)
        goal = next(command.target for command in service._navigation_commands
                    if command.completes_visible)
        await hub.publish(sample(5, goal.latitude_deg, goal.longitude_deg,
                                 goal.altitude_m))
        await wait_until(lambda: len(execution.completed_targets) == 1)
        assert execution.completed_targets == [plan.route.waypoints[0]]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_pause_during_recovery_preserves_pending_search():
    terrain, hub, vehicle, service, plan, execution = await create_running()
    try:
        await hub.publish(sample(3, *terrain.local_to_geo(8, 6), 20))
        await service.stop(execution.execution_id)
        vehicle.armed = False
        await hub.publish(sample(4, *terrain.local_to_geo(0, 0), 0))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)
        task = asyncio.create_task(service.continue_mission(execution.execution_id))
        await wait_until(lambda: len(vehicle.takeoff_calls) == 2)
        await hub.publish(sample(5, *terrain.local_to_geo(0, 0), vehicle.takeoff_calls[-1]))
        await task
        original_checkpoint = execution.resume_checkpoint
        await service.pause(execution.execution_id)
        await service.resume(execution.execution_id)
        endpoints = [service._recovery_commands[i]
                     for i in service._recovery_segment_ends]
        for i, point in enumerate(endpoints):
            await hub.publish(sample(i + 6, point.latitude_deg, point.longitude_deg,
                                     point.altitude_m))
            if i == len(endpoints) - 1:
                await wait_until(lambda: not service._recovery_commands)
            else:
                await wait_until(lambda: service._recovery_index >
                                 service._recovery_segment_ends[i])
        assert execution.resume_checkpoint == original_checkpoint
        assert execution.active_target == plan.route.waypoints[0]
        assert not execution.completed_targets
    finally:
        await service.shutdown()
