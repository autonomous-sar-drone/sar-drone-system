"""Terrain search continuation after a manual flight diversion."""

import asyncio

import pytest

from app.domain.models.mission_state import FlightActivity, MissionState
from test_terrain_mission_flow import create_running, sample, wait_until


async def _take_and_divert(grid, hub, vehicle, service, execution, sequence):
    await service.take_controls(execution.execution_id)
    original_anchor = service._route_departure_anchor

    async def send_manual_velocity(*_args):
        return None

    vehicle.send_manual_velocity = send_manual_velocity
    await service.open_manual_channel()
    displaced = grid.local_to_geo(0, -25)
    await hub.publish(sample(sequence, *displaced, 23))
    await service.give_controls(execution.execution_id)
    assert execution.state == MissionState.PAUSED
    return original_anchor


async def _finish_rejoin(hub, service, execution, start_sequence):
    endpoints = tuple(service._recovery_commands[i]
                      for i in service._recovery_segment_ends)
    assert endpoints
    for offset, point in enumerate(endpoints):
        await hub.publish(sample(start_sequence + offset, point.latitude_deg,
                                 point.longitude_deg, point.altitude_m))
        if offset == len(endpoints) - 1:
            await wait_until(lambda: not service._recovery_commands)
        else:
            await wait_until(lambda: service._recovery_index >
                             service._recovery_segment_ends[offset])
    assert execution.flight_activity == FlightActivity.SEARCHING
    assert service._route_departure_anchor is None


@pytest.mark.asyncio
async def test_early_takeover_returns_to_original_position_before_waypoint_one():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        early = service._navigation_commands[0].target
        await hub.publish(sample(3, early.latitude_deg, early.longitude_deg,
                                 early.altitude_m))
        anchor = await _take_and_divert(grid, hub, vehicle, service, execution, 4)
        assert anchor.completed_count == 0
        assert anchor.unfinished_target == plan.route.waypoints[0]
        assert execution.completed_targets == []

        await service.resume(execution.execution_id)
        assert execution.flight_activity == FlightActivity.REJOINING
        finish = service._recovery_commands[-1]
        assert (finish.latitude_deg, finish.longitude_deg) == (
            anchor.position.latitude_deg, anchor.position.longitude_deg,
        )
        await _finish_rejoin(hub, service, execution, 5)
        assert execution.completed_targets == []
        assert execution.active_target == plan.route.waypoints[0]

        first_waypoint = next(command.target for command in service._navigation_commands
                              if command.completes_visible)
        await hub.publish(sample(10, first_waypoint.latitude_deg,
                                 first_waypoint.longitude_deg, first_waypoint.altitude_m))
        await wait_until(lambda: len(execution.completed_targets) == 1)
        assert execution.completed_targets == [plan.route.waypoints[0]]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_low_altitude_takeover_preserves_xy_and_rejoins_at_safe_height():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        low = grid.local_to_geo(1, 0)
        await hub.publish(sample(3, *low, 17.6))
        anchor = await _take_and_divert(grid, hub, vehicle, service, execution, 4)
        assert anchor.position.altitude_m == 17.6
        await service.resume(execution.execution_id)
        finish = service._recovery_commands[-1]
        assert (finish.latitude_deg, finish.longitude_deg) == low
        assert finish.altitude_m >= (
            grid.height_geo(*low) + plan.planning_settings.search_altitude_m
        )
        assert service._route_departure_anchor.position.altitude_m == 17.6
        await _finish_rejoin(hub, service, execution, 5)
        assert not execution.completed_targets
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_mid_search_takeover_keeps_completed_waypoints():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        first = next(command.target for command in service._navigation_commands
                     if command.completes_visible)
        await hub.publish(sample(3, first.latitude_deg, first.longitude_deg,
                                 first.altitude_m))
        await wait_until(lambda: len(execution.completed_targets) == 1)
        second_leg = next(command.target for command in service._navigation_commands
                          if command.visible_index == 1)
        await hub.publish(sample(4, second_leg.latitude_deg, second_leg.longitude_deg,
                                 second_leg.altitude_m))
        anchor = await _take_and_divert(grid, hub, vehicle, service, execution, 5)
        assert anchor.completed_count == 1
        assert anchor.unfinished_target == plan.route.waypoints[1]
        await service.resume(execution.execution_id)
        assert execution.flight_activity == FlightActivity.REJOINING
        await _finish_rejoin(hub, service, execution, 6)
        assert execution.completed_targets == [plan.route.waypoints[0]]
        assert execution.active_target == plan.route.waypoints[1]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_takeover_during_rejoin_keeps_original_anchor():
    grid, hub, vehicle, service, _plan, execution = await create_running()
    try:
        first = service._navigation_commands[0].target
        await hub.publish(sample(3, first.latitude_deg, first.longitude_deg,
                                 first.altitude_m))
        anchor = await _take_and_divert(grid, hub, vehicle, service, execution, 4)
        await service.resume(execution.execution_id)
        assert execution.flight_activity == FlightActivity.REJOINING
        await service.take_controls(execution.execution_id)
        assert service._route_departure_anchor == anchor
        await hub.publish(sample(5, *grid.local_to_geo(-10, -20), 23))
        await service.give_controls(execution.execution_id)
        await service.resume(execution.execution_id)
        assert execution.flight_activity == FlightActivity.REJOINING
        final = service._recovery_commands[-1]
        assert (final.latitude_deg, final.longitude_deg) == (
            anchor.position.latitude_deg, anchor.position.longitude_deg,
        )
        await _finish_rejoin(hub, service, execution, 6)
        assert not execution.completed_targets
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_then_continue_uses_safe_altitude_for_low_original_anchor():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        low = grid.local_to_geo(1, 0)
        await hub.publish(sample(3, *low, 17.6))
        anchor = await _take_and_divert(grid, hub, vehicle, service, execution, 4)
        await service.stop(execution.execution_id)
        assert service._route_departure_anchor == anchor
        vehicle.armed = False
        home = grid.local_to_geo(0, 0)
        await hub.publish(sample(5, *home, 0))
        await wait_until(lambda: execution.state == MissionState.INTERRUPTED)

        continuation = asyncio.create_task(service.continue_mission(execution.execution_id))
        await wait_until(lambda: len(vehicle.takeoff_calls) == 2)
        await hub.publish(sample(6, *home, vehicle.takeoff_calls[-1]))
        await continuation
        assert execution.flight_activity == FlightActivity.REJOINING
        final = service._recovery_commands[-1]
        assert (final.latitude_deg, final.longitude_deg) == low
        assert final.altitude_m >= (
            grid.height_geo(*low) + plan.planning_settings.search_altitude_m
        )
    finally:
        await service.shutdown()
