import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode
from app.mission.mission_service import MissionService
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError


class FakeVehicle:
    def __init__(self) -> None:
        self.connected = True
        self.armed = False
        self.flight_mode = "STABILIZE"

        self.guided_calls = 0
        self.arm_calls = 0
        self.takeoff_calls: list[float] = []
        self.go_to_calls: list[Waypoint] = []
        self.hold_calls = 0
        self.rtl_calls = 0

        self.fail_guided = False
        self.fail_rtl = False

    def get_status(self) -> VehicleStatus:
        return VehicleStatus(
            connected=self.connected,
            armed=self.armed,
            flight_mode=self.flight_mode,
            last_heartbeat_utc=datetime.now(timezone.utc),
        )

    async def set_guided(self) -> None:
        self.guided_calls += 1

        if self.fail_guided:
            raise VehicleCommandError("GUIDED mode was rejected")

        self.flight_mode = "GUIDED"

    async def arm(self) -> None:
        self.arm_calls += 1
        self.armed = True

    async def takeoff(self, altitude_m: float) -> None:
        self.takeoff_calls.append(altitude_m)

    async def go_to(self, target: Waypoint) -> None:
        self.go_to_calls.append(target)

    async def hold(self) -> None:
        self.hold_calls += 1
        self.flight_mode = "BRAKE"

    async def return_to_launch(self) -> None:
        self.rtl_calls += 1

        if self.fail_rtl:
            raise VehicleCommandError("RTL was rejected")

        self.flight_mode = "RTL"


def make_search_area() -> SearchArea:
    return SearchArea(
        vertices=(
            GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7431),
            GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7428),
            GeoPoint(latitude_deg=30.2674, longitude_deg=-97.7428),
            GeoPoint(latitude_deg=30.2674, longitude_deg=-97.7431),
        )
    )


def make_sample(
    sequence: int,
    latitude_deg: float = 30.2672,
    longitude_deg: float = -97.7431,
    relative_altitude_m: float = 0.0,
    ground_speed_mps: float = 0.0,
    age_s: float = 0.0,
) -> TelemetrySample:
    return TelemetrySample(
        sequence=sequence,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        relative_altitude_m=relative_altitude_m,
        heading_deg=90.0,
        ground_speed_mps=ground_speed_mps,
    )


def make_service(
    hub: TelemetryHub,
    vehicle: FakeVehicle,
) -> MissionService:
    return MissionService(
        telemetry_hub=hub,
        default_planning_settings=PlanningSettings(
            search_altitude_m=25.0,
            lane_spacing_m=20.0,
        ),
        max_telemetry_age_s=3.0,
        max_transit_distance_m=2000.0,
        max_route_distance_m=10000.0,
        vehicle=vehicle,
        target_reached_tolerance_m=2.0,
        target_altitude_tolerance_m=1.0,
        takeoff_altitude_tolerance_m=1.0,
        takeoff_timeout_s=0.5,
        landing_altitude_tolerance_m=0.75,
        landing_speed_tolerance_mps=0.75,
    )


async def prepare_plan(
    service: MissionService,
    hub: TelemetryHub,
    sequence: int = 1,
):
    await hub.publish(make_sample(sequence=sequence))
    return await service.preview_route(make_search_area())


async def commence_running(
    service: MissionService,
    hub: TelemetryHub,
    plan_id: str,
    takeoff_sequence: int = 2,
):
    commence_task = asyncio.create_task(service.commence(plan_id))
    await asyncio.sleep(0)

    await hub.publish(
        make_sample(
            sequence=takeoff_sequence,
            relative_altitude_m=25.0,
        )
    )

    return await commence_task


async def wait_for_state(
    service: MissionService,
    state: MissionState,
    timeout_s: float = 0.5,
) -> None:
    async def wait() -> None:
        while service.state != state:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=timeout_s)


async def wait_for_active_target(
    service: MissionService,
    target: Waypoint,
    timeout_s: float = 0.5,
) -> None:
    async def wait() -> None:
        while True:
            execution = service.current_execution
            if execution is not None and execution.active_target == target:
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=timeout_s)


@pytest.mark.asyncio
async def test_commence_creates_execution_and_starts_first_target() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)

    execution = await commence_running(service, hub, plan.plan_id)

    assert execution.execution_id
    assert execution.plan_id == plan.plan_id
    assert execution.state == MissionState.RUNNING
    assert execution.active_target == plan.route.waypoints[0]
    assert execution.completed_targets == []

    assert vehicle.guided_calls == 1
    assert vehicle.arm_calls == 1
    assert vehicle.takeoff_calls == [25.0]
    assert vehicle.go_to_calls[-1] == plan.route.waypoints[0]

    # keep the initial ground position as well as the takeoff sample in history
    assert [sample.sequence for sample in execution.telemetry_history] == [1, 2]

    await service.shutdown()


@pytest.mark.asyncio
async def test_commence_rejects_stale_plan_id() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    await prepare_plan(service, hub)

    with pytest.raises(MissionExecutionError) as error_info:
        await service.commence("not-current")

    assert error_info.value.code == MissionExecutionErrorCode.PLAN_NOT_CURRENT


@pytest.mark.asyncio
async def test_commence_requires_connected_vehicle() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)

    vehicle.connected = False

    with pytest.raises(MissionExecutionError) as error_info:
        await service.commence(plan.plan_id)

    assert error_info.value.code == MissionExecutionErrorCode.VEHICLE_NOT_READY
    assert error_info.value.details == {"reason": "HEARTBEAT_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_failed_commence_before_arming_restores_planning_state() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)

    vehicle.fail_guided = True

    with pytest.raises(MissionExecutionError) as error_info:
        await service.commence(plan.plan_id)

    assert error_info.value.code == MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED
    assert service.state == MissionState.PLANNING
    assert service.current_execution is None
    assert service.proposed_plan == plan


@pytest.mark.asyncio
# BRAKE should hold without clearing the waypoint, resume sends that same target
async def test_pause_and_resume_preserve_and_resend_active_target() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    active_target = execution.active_target
    assert active_target is not None

    paused = await service.pause(execution.execution_id)

    assert paused.state == MissionState.PAUSED
    assert paused.active_target == active_target
    assert vehicle.hold_calls == 1
    assert vehicle.flight_mode == "BRAKE"

    resumed = await service.resume(execution.execution_id)

    assert resumed.state == MissionState.RUNNING
    assert resumed.active_target == active_target
    assert vehicle.flight_mode == "GUIDED"
    assert vehicle.go_to_calls[-1] == active_target

    await service.shutdown()


@pytest.mark.asyncio
# the checkpoint needs the aircraft position from before the RTL trip
async def test_stop_captures_checkpoint_before_confirmed_rtl() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    interrupted_target = execution.active_target
    assert interrupted_target is not None

    checkpoint_sample = make_sample(
        sequence=3,
        latitude_deg=30.26725,
        longitude_deg=-97.74305,
        relative_altitude_m=24.8,
        ground_speed_mps=2.0,
    )
    await hub.publish(checkpoint_sample)

    stopped = await service.stop(execution.execution_id)

    assert stopped.state == MissionState.RETURNING_HOME
    assert stopped.active_target is None
    assert stopped.resume_checkpoint is not None
    assert stopped.resume_checkpoint.telemetry == checkpoint_sample
    assert stopped.resume_checkpoint.active_target == interrupted_target
    assert vehicle.rtl_calls == 1
    assert vehicle.flight_mode == "RTL"

    await service.shutdown()


@pytest.mark.asyncio
async def test_stop_does_not_claim_returning_home_when_rtl_fails() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    vehicle.fail_rtl = True

    with pytest.raises(MissionExecutionError) as error_info:
        await service.stop(execution.execution_id)

    assert error_info.value.code == MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED
    assert execution.state == MissionState.RUNNING
    assert service.state == MissionState.RUNNING
    assert execution.active_target is not None

    await service.shutdown()


@pytest.mark.asyncio
async def test_background_guidance_error_does_not_block_stop() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    target = execution.active_target
    assert target is not None

    # lose connection just as guidance tries to advance
    # a later stop still needs to work once the connection recovers
    vehicle.connected = False
    await hub.publish(
        make_sample(
            sequence=3,
            latitude_deg=target.latitude_deg,
            longitude_deg=target.longitude_deg,
            relative_altitude_m=target.altitude_m,
        )
    )

    async def wait_for_background_error() -> None:
        while service.background_execution_error is None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_background_error(), timeout=0.5)

    vehicle.connected = True
    stopped = await service.stop(execution.execution_id)

    assert stopped.state == MissionState.RETURNING_HOME
    assert service.background_execution_error is None
    assert vehicle.rtl_calls == 1

    await service.shutdown()


@pytest.mark.asyncio
# a manual stop should land as INTERRUPTED and return to that spot on continue
async def test_stop_lands_as_interrupted_then_continue_returns_to_checkpoint() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    interrupted_target = execution.active_target
    assert interrupted_target is not None

    checkpoint_sample = make_sample(
        sequence=3,
        latitude_deg=30.26725,
        longitude_deg=-97.74305,
        relative_altitude_m=25.0,
        ground_speed_mps=2.0,
    )
    await hub.publish(checkpoint_sample)
    await service.stop(execution.execution_id)

    # returning home is not terminal until the landing is confirmed
    vehicle.armed = False
    await hub.publish(
        make_sample(
            sequence=4,
            latitude_deg=30.2672,
            longitude_deg=-97.7431,
            relative_altitude_m=0.1,
            ground_speed_mps=0.1,
        )
    )
    await wait_for_state(service, MissionState.INTERRUPTED)

    assert execution.state == MissionState.INTERRUPTED

    continue_task = asyncio.create_task(
        service.continue_mission(execution.execution_id)
    )
    await asyncio.sleep(0)

    await hub.publish(
        make_sample(
            sequence=5,
            relative_altitude_m=25.0,
        )
    )

    continued = await continue_task

    checkpoint_target = continued.active_target
    assert checkpoint_target is not None
    assert checkpoint_target.latitude_deg == checkpoint_sample.latitude_deg
    assert checkpoint_target.longitude_deg == checkpoint_sample.longitude_deg
    assert continued.execution_id == execution.execution_id
    assert continued.state == MissionState.RUNNING

    # hitting the checkpoint should restore the unfinished waypoint
    # it should not count as another completed search target
    await hub.publish(
        make_sample(
            sequence=6,
            latitude_deg=checkpoint_target.latitude_deg,
            longitude_deg=checkpoint_target.longitude_deg,
            relative_altitude_m=checkpoint_target.altitude_m,
        )
    )

    await wait_for_active_target(service, interrupted_target)

    assert execution.completed_targets == []
    assert execution.active_target == interrupted_target
    assert vehicle.go_to_calls[-1] == interrupted_target

    await service.shutdown()


@pytest.mark.asyncio
async def test_failed_continue_before_arming_restores_interrupted_state() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    await hub.publish(
        make_sample(
            sequence=3,
            relative_altitude_m=25.0,
            ground_speed_mps=2.0,
        )
    )
    await service.stop(execution.execution_id)

    vehicle.armed = False
    await hub.publish(
        make_sample(
            sequence=4,
            relative_altitude_m=0.1,
            ground_speed_mps=0.1,
        )
    )
    await wait_for_state(service, MissionState.INTERRUPTED)

    vehicle.fail_guided = True

    with pytest.raises(MissionExecutionError) as error_info:
        await service.continue_mission(execution.execution_id)

    assert error_info.value.code == MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED
    assert service.state == MissionState.INTERRUPTED
    assert execution.state == MissionState.INTERRUPTED
    assert execution.active_target is None

    await service.shutdown()


@pytest.mark.asyncio
async def test_target_progression_and_normal_completion() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    sequence = 3

    for target in plan.route.waypoints:
        await hub.publish(
            make_sample(
                sequence=sequence,
                latitude_deg=target.latitude_deg,
                longitude_deg=target.longitude_deg,
                relative_altitude_m=target.altitude_m,
                ground_speed_mps=3.0,
            )
        )
        sequence += 1
        await asyncio.sleep(0)

    await wait_for_state(service, MissionState.RETURNING_HOME)

    assert len(execution.completed_targets) == len(plan.route.waypoints)
    assert execution.active_target is None
    assert vehicle.rtl_calls == 1

    # low altitude does not mean landing if the drone still reports armed
    await hub.publish(
        make_sample(
            sequence=sequence,
            relative_altitude_m=0.1,
            ground_speed_mps=0.1,
        )
    )
    await asyncio.sleep(0)

    assert service.state == MissionState.RETURNING_HOME

    vehicle.armed = False
    await hub.publish(
        make_sample(
            sequence=sequence + 1,
            relative_altitude_m=0.1,
            ground_speed_mps=0.1,
        )
    )

    await wait_for_state(service, MissionState.COMPLETED)

    assert execution.state == MissionState.COMPLETED


@pytest.mark.asyncio
async def test_execution_history_preserves_every_subscribed_sample() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    # keep samples away from the target, this case is only about history
    for sequence in range(3, 13):
        await hub.publish(
            make_sample(
                sequence=sequence,
                latitude_deg=30.2660,
                longitude_deg=-97.7440,
                relative_altitude_m=25.0,
                ground_speed_mps=1.0,
            )
        )

    async def wait_for_history() -> None:
        while len(execution.telemetry_history) < 12:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_history(), timeout=0.5)

    assert [
        sample.sequence for sample in execution.telemetry_history
    ] == list(range(1, 13))

    await service.shutdown()


@pytest.mark.asyncio
# another stop during recovery should still preserve the original search target
async def test_second_stop_while_returning_to_checkpoint_keeps_search_target() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    original_search_target = execution.active_target
    assert original_search_target is not None

    first_checkpoint = make_sample(
        sequence=3,
        latitude_deg=30.26725,
        longitude_deg=-97.74305,
        relative_altitude_m=25.0,
        ground_speed_mps=2.0,
    )
    await hub.publish(first_checkpoint)
    await service.stop(execution.execution_id)

    vehicle.armed = False
    await hub.publish(
        make_sample(
            sequence=4,
            relative_altitude_m=0.1,
            ground_speed_mps=0.1,
        )
    )
    await wait_for_state(service, MissionState.INTERRUPTED)

    vehicle.armed = False
    continue_task = asyncio.create_task(
        service.continue_mission(execution.execution_id)
    )
    await asyncio.sleep(0)
    await hub.publish(
        make_sample(
            sequence=5,
            relative_altitude_m=25.0,
        )
    )
    await continue_task

    # stop again during recovery, before reaching the first checkpoint
    second_checkpoint = make_sample(
        sequence=6,
        latitude_deg=30.26722,
        longitude_deg=-97.74308,
        relative_altitude_m=25.0,
        ground_speed_mps=2.0,
    )
    await hub.publish(second_checkpoint)
    await service.stop(execution.execution_id)

    assert execution.resume_checkpoint is not None
    assert execution.resume_checkpoint.telemetry == second_checkpoint
    assert execution.resume_checkpoint.active_target == original_search_target

    await service.shutdown()


@pytest.mark.asyncio
async def test_backlogged_position_is_recorded_but_not_used_for_navigation() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    first_target = plan.route.waypoints[0]

    # old samples can pile up while an action holds the lock
    # keep them in history without using them to complete waypoints
    async with service._action_lock:
        await hub.publish(make_sample(
            sequence=10, latitude_deg=first_target.latitude_deg,
            longitude_deg=first_target.longitude_deg,
            relative_altitude_m=first_target.altitude_m,
        ))
        await hub.publish(make_sample(
            sequence=11, latitude_deg=30.2700,
            longitude_deg=-97.7500,
            relative_altitude_m=25.0,
        ))
        await asyncio.sleep(0)

    await asyncio.sleep(0.02)
    assert execution.active_target == first_target
    assert execution.completed_targets == []
    assert [sample.sequence for sample in execution.telemetry_history][-2:] == [10, 11]

    await hub.publish(make_sample(
        sequence=12, latitude_deg=first_target.latitude_deg,
        longitude_deg=first_target.longitude_deg,
        relative_altitude_m=first_target.altitude_m,
    ))

    async def reached_target() -> None:
        while not execution.completed_targets:
            await asyncio.sleep(0)

    await asyncio.wait_for(reached_target(), timeout=0.5)
    assert execution.completed_targets == [first_target]
    await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("final_state", [MissionState.INTERRUPTED, MissionState.COMPLETED])
async def test_replan_from_terminal_state_archives_previous_execution(final_state) -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    old_plan = await prepare_plan(service, hub)
    previous = MissionExecution(
        execution_id="old-execution", plan_id=old_plan.plan_id,
        state=final_state,
        telemetry_history=[make_sample(sequence=15)],
    )
    service._current_execution = previous
    service._state = final_state

    new_plan = await service.preview_route(make_search_area())

    assert new_plan.plan_id != old_plan.plan_id
    assert service.state == MissionState.PLANNING
    assert service.current_execution is None
    assert service.past_executions[-1] is previous
    assert [sample.sequence for sample in previous.telemetry_history] == [15]


@pytest.mark.asyncio
async def test_mission_service_publishes_preflight_running_and_new_target() -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    updates, _ = mission_hub.subscribe()
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)

    preflight = updates.get_nowait()
    running = updates.get_nowait()
    assert preflight.state == MissionState.PREFLIGHT_CHECK
    assert preflight.active_target is None
    assert running.state == MissionState.RUNNING
    assert running.active_target == plan.route.waypoints[0]

    first = plan.route.waypoints[0]
    await hub.publish(make_sample(
        sequence=20,
        latitude_deg=first.latitude_deg,
        longitude_deg=first.longitude_deg,
        relative_altitude_m=first.altitude_m,
    ))
    await wait_for_active_target(service, plan.route.waypoints[1])

    update = updates.get_nowait()
    assert update.state == MissionState.RUNNING
    assert update.active_target == plan.route.waypoints[1]
    assert running.active_target == first
    assert execution.completed_targets == [first]
    await service.shutdown()
    mission_hub.unsubscribe(updates)


@pytest.mark.asyncio
async def test_failed_preflight_resets_mission_hub_snapshot() -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    updates, _ = mission_hub.subscribe()
    plan = await prepare_plan(service, hub)
    vehicle.fail_guided = True

    with pytest.raises(MissionExecutionError):
        await service.commence(plan.plan_id)

    assert updates.get_nowait().state == MissionState.PREFLIGHT_CHECK
    assert updates.get_nowait().state == MissionState.PLANNING
    reconnect_queue, latest = mission_hub.subscribe()
    assert latest is None
    assert service.current_execution is None
    mission_hub.unsubscribe(updates)
    mission_hub.unsubscribe(reconnect_queue)

@pytest.mark.asyncio
async def test_navigation_uses_current_position_despite_queued_history() -> None:
    hub = TelemetryHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    target = execution.active_target
    assert target is not None

    # build up a queue of history samples while navigation is blocked
    # once free, it should check the latest aircraft position
    async with service._action_lock:
        for sequence in range(3, 503):
            await hub.publish(make_sample(
                sequence=sequence,
                latitude_deg=target.latitude_deg if sequence == 502 else 30.2672,
                longitude_deg=target.longitude_deg if sequence == 502 else -97.7431,
                relative_altitude_m=25.0,
            ))

    await wait_for_active_target(service, plan.route.waypoints[1], timeout_s=2.0)

    async def fully_recorded() -> None:
        while len(execution.telemetry_history) < 502:
            await asyncio.sleep(0)

    await asyncio.wait_for(fully_recorded(), timeout=2.0)
    assert [sample.sequence for sample in execution.telemetry_history] == list(range(1, 503))
    assert execution.completed_targets == [target]
    assert vehicle.go_to_calls[-1] == plan.route.waypoints[1]
    await service.shutdown()


@pytest.mark.asyncio
# unexpected disarm stops guidance, but the monitor keeps saving telemetry
async def test_runtime_error_on_unexpected_disarm_published_and_recording_continues() -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    updates, _ = mission_hub.subscribe()

    vehicle.armed = False
    await hub.publish(make_sample(sequence=3, relative_altitude_m=10.0))

    async def error_reported() -> None:
        while service.background_execution_error is None:
            await asyncio.sleep(0)

    await asyncio.wait_for(error_reported(), timeout=1.0)
    error_event = await asyncio.wait_for(updates.get(), timeout=1.0)
    assert error_event.state == MissionState.RUNNING
    assert error_event.runtime_error is not None
    assert error_event.runtime_error.details == {"reason": "UNEXPECTED_DISARM"}

    await hub.publish(make_sample(sequence=4, relative_altitude_m=0.0))
    async def recorded() -> None:
        while execution.telemetry_history[-1].sequence != 4:
            await asyncio.sleep(0)
    await asyncio.wait_for(recorded(), timeout=1.0)
    assert len(vehicle.go_to_calls) == 1

    reconnect, latest = mission_hub.subscribe()
    assert latest is not None and latest.runtime_error == error_event.runtime_error
    mission_hub.unsubscribe(updates)
    mission_hub.unsubscribe(reconnect)
    await service.shutdown()


@pytest.mark.asyncio
async def test_runtime_error_on_background_guidance_failure_is_published() -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    updates, _ = mission_hub.subscribe()
    target = execution.active_target
    assert target is not None

    vehicle.connected = False
    await hub.publish(make_sample(
        sequence=3, latitude_deg=target.latitude_deg,
        longitude_deg=target.longitude_deg, relative_altitude_m=25.0,
    ))

    async def failed() -> None:
        while service.background_execution_error is None:
            await asyncio.sleep(0)
    await asyncio.wait_for(failed(), timeout=1.0)
    event = await asyncio.wait_for(updates.get(), timeout=1.0)
    assert event.runtime_error is not None
    assert event.runtime_error.code == "VEHICLE_NOT_READY"
    assert event.active_target == target
    await service.shutdown()
    mission_hub.unsubscribe(updates)

@pytest.mark.asyncio
async def test_unexpected_monitor_exception_surfaces_as_runtime_error(monkeypatch) -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    updates, _ = mission_hub.subscribe()

    async def broken_handler(execution, sample) -> None:
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(service, "_handle_running_sample", broken_handler)
    await hub.publish(make_sample(sequence=3, relative_altitude_m=25.0))

    async def reported() -> None:
        while service.background_execution_error is None:
            await asyncio.sleep(0)
    await asyncio.wait_for(reported(), timeout=1.0)
    event = await asyncio.wait_for(updates.get(), timeout=1.0)
    assert event.runtime_error is not None
    assert event.runtime_error.code == "MISSION_MONITOR_FAILED"
    assert event.state == MissionState.RUNNING
    assert service.current_execution is execution
    mission_hub.unsubscribe(updates)
    await service.shutdown()


@pytest.mark.asyncio
# disarming in BRAKE also needs an error, without advancing the paused waypoint
async def test_unexpected_disarm_while_paused_is_reported_without_advancing_route() -> None:
    hub = TelemetryHub()
    mission_hub = MissionHub()
    vehicle = FakeVehicle()
    service = make_service(hub, vehicle)
    service._mission_hub = mission_hub
    plan = await prepare_plan(service, hub)
    execution = await commence_running(service, hub, plan.plan_id)
    paused = await service.pause(execution.execution_id)
    assert paused.state == MissionState.PAUSED
    assert vehicle.flight_mode == "BRAKE"
    target = paused.active_target
    assert target is not None

    updates, _ = mission_hub.subscribe()
    # being near a target while paused must not advance the route
    await hub.publish(make_sample(
        sequence=3,
        latitude_deg=target.latitude_deg,
        longitude_deg=target.longitude_deg,
        relative_altitude_m=target.altitude_m,
    ))
    async def recorded_first() -> None:
        while execution.telemetry_history[-1].sequence != 3:
            await asyncio.sleep(0)
    await asyncio.wait_for(recorded_first(), timeout=1.0)
    assert execution.active_target == target
    assert execution.completed_targets == []
    assert len(vehicle.go_to_calls) == 1
    assert service.background_execution_error is None

    vehicle.armed = False
    await hub.publish(make_sample(sequence=4, relative_altitude_m=0.0))
    async def failed() -> None:
        while service.background_execution_error is None:
            await asyncio.sleep(0)
    await asyncio.wait_for(failed(), timeout=1.0)

    error_event = await asyncio.wait_for(updates.get(), timeout=1.0)
    assert error_event.state == MissionState.PAUSED
    assert error_event.active_target == target
    assert error_event.runtime_error is not None
    assert error_event.runtime_error.code == "VEHICLE_NOT_READY"
    assert error_event.runtime_error.details == {"reason": "UNEXPECTED_DISARM"}
    assert execution.telemetry_history[-1].sequence == 4
    assert len(vehicle.go_to_calls) == 1
    assert execution.completed_targets == []

    reconnect, latest = mission_hub.subscribe()
    assert latest is not None
    assert latest.runtime_error == error_event.runtime_error
    assert latest.state == MissionState.PAUSED
    with pytest.raises(MissionExecutionError) as error_info:
        await service.resume(execution.execution_id)
    assert error_info.value.code == MissionExecutionErrorCode.VEHICLE_NOT_READY
    mission_hub.unsubscribe(updates)
    mission_hub.unsubscribe(reconnect)
    await service.shutdown()
