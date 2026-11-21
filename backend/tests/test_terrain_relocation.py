import asyncio
import math

import pytest

from app.domain.models.mission_state import MissionState
from app.domain.models.route import Waypoint
from app.geospatial.terrain_relocation import (
    plan_terrain_relocation, plan_terrain_relocation_chain,
)
from test_terrain_mission_flow import create_running, sample, wait_until
from test_terrain_navigation import terrain_grid, waypoint


def test_low_manual_departure_stages_climb_before_horizontal_rejoin():
    grid = terrain_grid()
    departure = waypoint(grid, 0, 0, 7)
    destination = waypoint(grid, 35, 0, 23)
    route = plan_terrain_relocation(grid, departure, destination, 18)
    assert route.climb_target is not None
    assert route.segment_ends[0] == 0
    assert route.commands[0].latitude_deg == departure.latitude_deg
    assert route.commands[0].longitude_deg == departure.longitude_deg
    assert route.commands[0].altitude_m >= 19
    assert route.commands[-1].latitude_deg == destination.latitude_deg
    assert route.commands[-1].longitude_deg == destination.longitude_deg
    for target in route.commands[1:]:
        ground = grid.height_geo(target.latitude_deg, target.longitude_deg)
        assert target.altitude_m >= ground + 18
    for a, b in zip(route.commands[:-1], route.commands[1:]):
        distance = math.dist(grid.geo_to_local(a.latitude_deg, a.longitude_deg),
                             grid.geo_to_local(b.latitude_deg, b.longitude_deg))
        if distance > 0.01:
            assert abs(a.altitude_m - b.altitude_m) <= 0.325 * distance + 1e-3


def test_historical_viewpoint_altitude_is_reached_after_corridor():
    grid = terrain_grid()
    departure = waypoint(grid, 0, 0, 19)
    end = waypoint(grid, 30, 0, 22)  # local ground is 4 m, so 18 m AGL
    route = plan_terrain_relocation(grid, departure, end, 18)
    assert route.commands[-1] == end
    assert route.segment_ends[-1] == len(route.commands) - 1
    assert len(route.segment_ends) >= 2  # safe transit, then final stationary descent
    last_transit = route.commands[route.segment_ends[-2]]
    assert last_transit.altitude_m >= 23
    assert (last_transit.latitude_deg, last_transit.longitude_deg) == (
        end.latitude_deg, end.longitude_deg,
    )


def test_relocation_rejects_unsafe_or_unmapped_endpoints():
    grid = terrain_grid()
    start = waypoint(grid, 0, 0, 19)
    with pytest.raises(ValueError, match="below the nominal"):
        plan_terrain_relocation(grid, start, waypoint(grid, 35, 0, 20), 18)
    with pytest.raises(ValueError, match="manual altitude"):
        plan_terrain_relocation(grid, waypoint(grid, 0, 0, 4), start, 18)
    with pytest.raises(ValueError, match="outside"):
        plan_terrain_relocation(grid, start, waypoint(grid, 122, 0, 24), 18)
    with pytest.raises(ValueError, match="outside"):
        plan_terrain_relocation(grid, waypoint(grid, 122, 0, 25), start, 18)


def test_multiple_relocation_legs_preserve_arrivals_and_stage_boundaries():
    grid = terrain_grid()
    a = waypoint(grid, 0, 0, 19)
    b = waypoint(grid, 30, 0, 23)
    c = waypoint(grid, 0, 30, 19)
    route = plan_terrain_relocation_chain(grid, a, (b, c), 18)
    assert route.commands[route.segment_ends[-1]].latitude_deg == c.latitude_deg
    assert any(route.commands[index].latitude_deg == b.latitude_deg
               and route.commands[index].longitude_deg == b.longitude_deg
               for index in route.segment_ends)
    assert tuple(sorted(set(route.segment_ends))) == route.segment_ends


@pytest.mark.asyncio
async def test_original_anchor_survives_displacement_and_rejoin():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        original = grid.local_to_geo(4, 0)
        await hub.publish(sample(3, *original, 19))
        anchor = await service.capture_route_departure_anchor(execution.execution_id)
        assert anchor.completed_count == 0
        assert anchor.unfinished_target == plan.route.waypoints[0]
        await hub.publish(sample(4, *grid.local_to_geo(6, 2), 19))
        assert await service.capture_route_departure_anchor(execution.execution_id) == anchor
        await service.pause(execution.execution_id)
        displaced = grid.local_to_geo(30, 30)
        await hub.publish(sample(5, *displaced, 23))
        await service.resume(execution.execution_id)
        assert service._recovery_commands[-1].latitude_deg == anchor.position.latitude_deg
        assert service._recovery_commands[-1].longitude_deg == anchor.position.longitude_deg
        assert execution.completed_targets == []
        assert execution.active_target == plan.route.waypoints[0]
        for i, target_index in enumerate(service._recovery_segment_ends):
            target = service._recovery_commands[target_index]
            await hub.publish(sample(6 + i, target.latitude_deg, target.longitude_deg,
                                     target.altitude_m))
            if i == len(service._recovery_segment_ends) - 1:
                await wait_until(lambda: not service._recovery_commands)
            else:
                await wait_until(lambda: service._recovery_index > target_index)
        assert service._route_departure_anchor is None
        assert execution.completed_targets == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_manual_descent_resume_holds_xy_until_altitude_recovered():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.pause(execution.execution_id)
        low = grid.local_to_geo(2, 0)
        await hub.publish(sample(3, *low, 7))
        await service.resume(execution.execution_id)
        first = service._recovery_commands[0]
        assert service._recovery_climb_target == first
        assert service._recovery_segment_ends[0] == 0
        assert vehicle.go_to_calls[-1] == first
        await hub.publish(sample(4, *low, 9))
        await asyncio.sleep(0.03)
        assert service.background_execution_error is None
        assert execution.state == MissionState.RUNNING
        await hub.publish(sample(5, *low, first.altitude_m))
        await wait_until(lambda: service._recovery_index > 0)
        assert service._recovery_climb_target is None
        assert service.background_execution_error is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_continue_uses_sticky_anchor_instead_of_stop_position():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        await hub.publish(sample(3, *grid.local_to_geo(3, 0), 19))
        anchor = await service.capture_route_departure_anchor(execution.execution_id)
        await service.pause(execution.execution_id)
        await hub.publish(sample(4, *grid.local_to_geo(30, 20), 23))
        await service.resume(execution.execution_id)
        stop_position = grid.local_to_geo(20, 15)
        await hub.publish(sample(5, *stop_position, 23))
        await service.stop(execution.execution_id)
        assert service._route_departure_anchor == anchor
        assert execution.resume_checkpoint.telemetry.latitude_deg == stop_position[0]
        vehicle.armed = False
        await hub.publish(sample(6, *grid.local_to_geo(0, 0), 0))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)
        task = asyncio.create_task(service.continue_mission(execution.execution_id))
        await wait_until(lambda: len(vehicle.takeoff_calls) == 2)
        await hub.publish(sample(7, *grid.local_to_geo(0, 0), vehicle.takeoff_calls[-1]))
        await task
        assert service._recovery_commands[-1].latitude_deg == anchor.position.latitude_deg
        assert service._recovery_commands[-1].longitude_deg == anchor.position.longitude_deg
        assert execution.active_target == anchor.unfinished_target
        assert execution.completed_targets == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_cannot_capture_departure_when_paused():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.pause(execution.execution_id)
        with pytest.raises(Exception, match="capture_route_departure_anchor"):
            await service.capture_route_departure_anchor(execution.execution_id)
        assert service._route_departure_anchor is None
    finally:
        await service.shutdown()


def test_dense_corridor_clearance_on_reverse_hill_and_diagonal():
    grid = terrain_grid()
    for a, b in (((35, 0), (0, 0)), ((35, 35), (0, -30))):
        departure = waypoint(grid, *a, 23)
        destination = waypoint(grid, *b, 23)
        route = plan_terrain_relocation(grid, departure, destination, 18)
        ground_leg_end = route.segment_ends[-1]
        if (len(route.segment_ends) > 1 and
                route.commands[route.segment_ends[-1]].altitude_m !=
                route.commands[route.segment_ends[-2]].altitude_m):
            ground_leg_end = route.segment_ends[-2]
        points = (departure,) + route.commands[:ground_leg_end + 1]
        for p, q in zip(points, points[1:]):
            pn, pw = grid.geo_to_local(p.latitude_deg, p.longitude_deg)
            qn, qw = grid.geo_to_local(q.latitude_deg, q.longitude_deg)
            steps = max(1, math.ceil(math.hypot(qn-pn, qw-pw) / 0.25))
            for step in range(steps + 1):
                fraction = step / steps
                north, west = pn + (qn-pn)*fraction, pw + (qw-pw)*fraction
                altitude = p.altitude_m + (q.altitude_m-p.altitude_m)*fraction
                assert altitude - grid.height_local(north, west) >= 18 - 1e-3


@pytest.mark.asyncio
async def test_new_anchor_is_refused_mid_recovery_without_an_existing_one():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        await service.pause(execution.execution_id)
        await hub.publish(sample(3, *grid.local_to_geo(3, 2), 20))
        await service.resume(execution.execution_id)
        assert service._recovery_commands
        with pytest.raises(Exception, match="capture_route_departure_anchor"):
            await service.capture_route_departure_anchor(execution.execution_id)
        assert service._route_departure_anchor is None
    finally:
        await service.shutdown()
