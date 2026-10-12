import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode
from app.mission.mission_service import MissionService
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
        self.flight_mode = "LOITER"

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

    # The initial ground sample and takeoff sample are both preserved.
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
    assert vehicle.flight_mode == "LOITER"

    resumed = await service.resume(execution.execution_id)

    assert resumed.state == MissionState.RUNNING
    assert resumed.active_target == active_target
    assert vehicle.flight_mode == "GUIDED"
    assert vehicle.go_to_calls[-1] == active_target

    await service.shutdown()


@pytest.mark.asyncio
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

    # Simulate connection loss exactly when guidance would advance. The
    # background monitor records the error but must not permanently block a
    # later recovery Stop once the connection is available again.
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

    # RTL is only terminal after telemetry indicates the return/landing finished.
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

    # Arriving back at the precise checkpoint should resume the interrupted
    # search target without counting the checkpoint as a completed route point.
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

    # Low altitude/speed alone is not enough to declare landing while the
    # vehicle still reports itself armed.
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

    # Keep these points away from the active target so this test only exercises
    # history collection rather than route progression.
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

    # The vehicle is now returning to the first checkpoint, not yet to the
    # original search target. Stop again before that checkpoint is reached.
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
