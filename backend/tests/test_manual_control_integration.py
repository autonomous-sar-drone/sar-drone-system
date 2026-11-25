"""Backend authority and input handoff tests; no actual drone is flown."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import FlightActivity, HoldReason, MissionState
from app.domain.models.route import AltitudeReference, PlanningSettings, Waypoint
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.geospatial.terrain_grid import TerrainGrid
from app.mission.errors import MissionExecutionError
from app.mission.mission_service import MissionService
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError


class ManualVehicle:
    def __init__(self):
        self.connected = True
        self.armed = True
        self.flight_mode = "GUIDED"
        self.commands: list[tuple] = []
        self.fail_guided = False
        self.fail_brake = False
        self.fail_rtl = False
        self.fail_movement = False

    def get_status(self):
        return VehicleStatus(self.connected, self.armed, self.flight_mode,
                             datetime.now(timezone.utc))

    async def set_guided(self):
        self.commands.append(("set_guided",))
        if self.fail_guided:
            raise VehicleCommandError("GUIDED unavailable")
        self.flight_mode = "GUIDED"

    async def hold(self):
        self.commands.append(("hold",))
        if self.fail_brake:
            raise VehicleCommandError("BRAKE unavailable")
        self.flight_mode = "BRAKE"

    async def return_to_launch(self):
        self.commands.append(("rtl",))
        if self.fail_rtl:
            raise VehicleCommandError("RTL unavailable")
        self.flight_mode = "RTL"

    async def send_manual_velocity(self, forward_mps, right_mps, up_mps, yaw_rate_deg_s):
        self.commands.append(("velocity", forward_mps, right_mps, up_mps, yaw_rate_deg_s))
        if self.fail_movement:
            raise VehicleCommandError("velocity unavailable")
        if not self.connected or not self.armed or self.flight_mode != "GUIDED":
            raise VehicleCommandError("not in GUIDED")


def terrain():
    heights = tuple(tuple(0.0 for _ in range(21)) for _ in range(21))
    grid = TerrainGrid("control-test", 30.2672, -97.7431, 150, 200, 200, 10, heights)
    grid.validate()
    return grid


def sample(*, altitude=18.0, age_s=0.0, heading=90.0):
    return TelemetrySample(
        1, datetime.now(timezone.utc) - timedelta(seconds=age_s),
        30.2672, -97.7431, altitude, heading, 0.0,
    )


async def service_with_running():
    hub, events, vehicle = TelemetryHub(), MissionHub(), ManualVehicle()
    await hub.publish(sample())
    service = MissionService(
        telemetry_hub=hub,
        mission_hub=events,
        vehicle=vehicle,
        default_planning_settings=PlanningSettings(search_altitude_m=18, lane_spacing_m=20),
        max_telemetry_age_s=3,
        max_transit_distance_m=1000,
        max_route_distance_m=10000,
        terrain_grid=terrain(),
    )
    execution = MissionExecution(
        "exec-1", "plan-1", MissionState.RUNNING,
        active_target=Waypoint(30.26725, -97.7431, 18, AltitudeReference.HOME_RELATIVE),
        flight_activity=FlightActivity.SEARCHING,
    )
    service._state = MissionState.RUNNING
    service._current_execution = execution
    return service, hub, vehicle, events, execution


def input_frame(epoch, sequence=1, *, forward=0, right=0, up=0, yaw=0, **kwargs):
    payload = dict(
        executionId="exec-1", controlEpoch=epoch, sequence=sequence,
        clientSentAtMs=int(datetime.now(timezone.utc).timestamp() * 1000),
        forward=forward, right=right, up=up, yaw=yaw,
    )
    payload.update(kwargs)
    return payload



@pytest.mark.asyncio
async def test_take_pauses_autonomous_before_socket_can_send_any_velocity():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    assert execution.state == MissionState.PAUSED
    assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
    assert execution.manual_control_ready is False
    assert vehicle.flight_mode == "BRAKE"
    assert vehicle.commands == [("hold",)]
    assert svc._route_departure_anchor is not None
    assert svc._route_departure_anchor.unfinished_target == execution.active_target

    ready = await svc.open_manual_channel()
    assert ready["executionId"] == "exec-1"
    assert ready["controlEpoch"]
    assert ready["sendIntervalMs"] == 100
    assert execution.manual_control_ready is True
    assert vehicle.commands == [
        ("hold",), ("set_guided",), ("velocity", 0.0, 0.0, 0.0, 0.0),
    ]


@pytest.mark.asyncio
async def test_manual_movement_is_neutral_until_new_epoch_gets_released_keys():
    svc, _, vehicle, events, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    first = await svc.manual_channel_input(epoch, input_frame(epoch, 1, forward=1))
    assert first["accepted"] is True
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    released = await svc.manual_channel_input(epoch, input_frame(epoch, 2))
    assert released["accepted"] is True
    move = await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1, up=1, yaw=1))
    assert move == {"sequence": 3, "accepted": True}
    assert vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.75, 20.0)
    assert events.subscribe()[1].flight_activity == FlightActivity.MANUAL_CONTROL


@pytest.mark.asyncio
async def test_unauthorized_socket_and_wrong_epoch_cannot_drive_vehicle():
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    with pytest.raises(MissionExecutionError):
        await svc.open_manual_channel()
    before = len(vehicle.commands)
    assert (await svc.manual_channel_input("old-epoch", input_frame(epoch))) == {
        "sequence": 0, "accepted": False, "revoked": True,
    }
    assert len(vehicle.commands) == before
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 1, controlEpoch="replayed"))).get("accepted") is False
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_give_controls_releases_socket_and_confirmed_brake_no_resume():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    await svc.give_controls("exec-1")
    assert execution.state == MissionState.PAUSED
    assert execution.flight_activity == FlightActivity.HOLDING
    assert execution.hold_reason == HoldReason.MANUAL_RELEASE
    assert execution.manual_control_ready is False
    assert vehicle.flight_mode == "BRAKE"
    assert vehicle.commands[-2:] == [("velocity", 0.0, 0.0, 0.0, 0.0), ("hold",)]
    before = len(vehicle.commands)
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1)))["revoked"]
    assert len(vehicle.commands) == before


@pytest.mark.asyncio
async def test_take_rejected_when_returning_home_or_not_armed():
    svc, _, vehicle, _, execution = await service_with_running()
    execution.state = MissionState.RETURNING_HOME
    svc._state = MissionState.RETURNING_HOME
    with pytest.raises(MissionExecutionError):
        await svc.take_controls("exec-1")
    assert vehicle.commands == []
    execution.state = MissionState.RUNNING
    svc._state = MissionState.RUNNING
    vehicle.armed = False
    with pytest.raises(MissionExecutionError):
        await svc.take_controls("exec-1")
    assert vehicle.commands == []


@pytest.mark.asyncio
async def test_failed_brake_does_not_enable_manual_or_pause_search():
    svc, _, vehicle, _, execution = await service_with_running()
    vehicle.fail_brake = True
    with pytest.raises(MissionExecutionError):
        await svc.take_controls("exec-1")
    assert execution.state == MissionState.RUNNING
    assert execution.flight_activity == FlightActivity.SEARCHING
    assert execution.manual_control_ready is False
    assert svc._route_departure_anchor is None


@pytest.mark.asyncio
async def test_failed_guided_on_socket_does_not_issue_manual_ready():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    vehicle.fail_guided = True
    with pytest.raises(MissionExecutionError):
        await svc.open_manual_channel()
    assert execution.manual_control_ready is False
    assert vehicle.flight_mode == "BRAKE"
    assert svc._manual_epoch is None
    assert vehicle.commands[-1] == ("hold",)


@pytest.mark.asyncio
async def test_stop_preempts_manual_and_old_input_cannot_cancel_rtl():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    await svc.stop("exec-1")
    assert execution.state == MissionState.RETURNING_HOME
    assert vehicle.flight_mode == "RTL"
    assert execution.manual_control_ready is False
    assert execution.flight_activity is None
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1)))["revoked"]
    assert vehicle.commands[-2:] == [("velocity", 0.0, 0.0, 0.0, 0.0), ("rtl",)]


@pytest.mark.asyncio
async def test_camera_loss_preempts_manual_and_triggers_rtl():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    assert await svc.interrupt_due_to_camera_loss()
    assert execution.state == MissionState.RETURNING_HOME
    assert execution.interruption_reason == "CAMERA_UNAVAILABLE"
    assert vehicle.flight_mode == "RTL"
    assert (await svc.manual_channel_input(epoch, input_frame(epoch)))["revoked"]


@pytest.mark.asyncio
async def test_socket_close_neutral_brake_and_reconnect_new_epoch():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    first = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(first, input_frame(first))
    await svc.manual_channel_input(first, input_frame(first, 2, forward=1))
    await svc.close_manual_channel(first)
    assert vehicle.flight_mode == "BRAKE"
    assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
    assert execution.manual_control_ready is False
    assert vehicle.commands[-2:] == [("velocity", 0.0, 0.0, 0.0, 0.0), ("hold",)]
    second = (await svc.open_manual_channel())["controlEpoch"]
    assert second != first
    assert (await svc.manual_channel_input(first, input_frame(first, 3, forward=1)))["revoked"]
    assert (await svc.manual_channel_input(second, input_frame(second, 1, forward=1)))["accepted"]
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    await svc.close_manual_channel(second)


@pytest.mark.asyncio
async def test_watchdog_timeout_fails_closed_and_needs_new_socket(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    t[0] += 0.55
    # A brief packet stall sends zero velocity but must not bounce to BRAKE.
    assert await svc.manual_watchdog_tick(epoch) is True
    assert execution.manual_control_ready is True
    assert vehicle.flight_mode == "GUIDED"
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    t[0] += svc._manual_limits.session_silence_timeout_s
    assert await svc.manual_watchdog_tick(epoch) is False
    assert execution.manual_control_ready is False
    assert vehicle.flight_mode == "BRAKE"
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1)))["revoked"]


@pytest.mark.asyncio
async def test_watchdog_stale_telemetry_sends_neutral_without_revoking_socket():
    svc, hub, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    await hub.publish(sample(age_s=2.0))
    assert await svc.manual_watchdog_tick(epoch)
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    assert execution.manual_control_ready
    await hub.publish(sample())
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1)))["accepted"]
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    # Explicit release restores the ability to move; stale W was never replayed.
    await svc.manual_channel_input(epoch, input_frame(epoch, 4))
    await svc.manual_channel_input(epoch, input_frame(epoch, 5, forward=1))
    assert vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_terrain_floor_requires_climb_and_emits_feedback_transition():
    svc, hub, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch))
    await hub.publish(sample(altitude=5.1))
    blocked = await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    assert blocked["feedback"] == "TERRAIN_MIN"
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    climbing = await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1, up=1))
    assert climbing["accepted"]
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.75, 0.0)
    await hub.publish(sample(altitude=12.0))
    clear = await svc.manual_channel_input(epoch, input_frame(epoch, 4))
    assert clear["clearFeedback"] == "TERRAIN_MIN"
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 5, forward=1)))["accepted"]
    assert vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_neutral_on_rejected_stale_frame_and_invalid_axes():
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    stale = input_frame(epoch, 3, forward=1)
    stale["clientSentAtMs"] -= 5000
    assert (await svc.manual_channel_input(epoch, stale))["accepted"] is False
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    invalid = input_frame(epoch, 4, forward=99)
    assert (await svc.manual_channel_input(epoch, invalid))["accepted"] is False
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_resume_blocked_while_manual_but_paused_hold_can_resume():
    svc, _, _, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    with pytest.raises(MissionExecutionError):
        await svc.resume("exec-1")
    # The next increment handles original-anchor route rejoin. Here we check
    # only that Give leaves the aircraft safely held.
    await svc.give_controls("exec-1")
    assert execution.state == MissionState.PAUSED
    assert execution.hold_reason == HoldReason.MANUAL_RELEASE


@pytest.mark.asyncio
async def test_guided_velocity_failure_revokes_socket():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    vehicle.fail_movement = True
    with pytest.raises(MissionExecutionError):
        await svc.manual_channel_input(epoch, input_frame(epoch))
    assert execution.manual_control_ready is False
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1)))["revoked"]


@pytest.mark.asyncio
async def test_no_manual_movement_after_unexpected_rtl_mode_change():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    vehicle.flight_mode = "RTL"
    assert await svc.manual_watchdog_tick(epoch) is False
    assert execution.manual_control_ready is False
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, forward=1)))["revoked"]
    assert not any(command[0] == "velocity" and command[1] > 0 for command in vehicle.commands)


@pytest.mark.asyncio
async def test_shutdown_brakes_manual_without_rtl():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    await svc.open_manual_channel()
    await svc.shutdown()
    assert execution.manual_control_ready is False
    assert vehicle.flight_mode == "BRAKE"
    assert ("rtl",) not in vehicle.commands

@pytest.mark.asyncio
async def test_native_rtl_is_never_cancelled_by_late_input_or_socket_cleanup():
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    vehicle.flight_mode = "RTL"
    before = len(vehicle.commands)
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 1, forward=1)))["revoked"]
    await svc.close_manual_channel(epoch)
    assert vehicle.flight_mode == "RTL"
    assert len(vehicle.commands) == before


@pytest.mark.asyncio
async def test_native_rtl_is_never_cancelled_by_socket_close():
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    vehicle.flight_mode = "RTL"
    before = len(vehicle.commands)
    await svc.close_manual_channel(epoch)
    assert vehicle.flight_mode == "RTL"
    assert len(vehicle.commands) == before


@pytest.mark.asyncio
async def test_stop_from_manual_with_stale_position_still_requests_rtl():
    svc, hub, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await hub.publish(sample(age_s=20))
    await svc.stop("exec-1")
    assert vehicle.flight_mode == "RTL"
    assert execution.state == MissionState.RETURNING_HOME
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 1)))["revoked"]
    assert execution.resume_checkpoint is None  # No false 'fresh' checkpoint


@pytest.mark.asyncio
async def test_stale_telemetry_refuses_take_without_sending_hold():
    svc, hub, vehicle, _, _ = await service_with_running()
    await hub.publish(sample(age_s=2))
    with pytest.raises(MissionExecutionError):
        await svc.take_controls("exec-1")
    assert vehicle.commands == []


@pytest.mark.asyncio
async def test_give_attempts_brake_even_when_neutral_send_fails():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    await svc.open_manual_channel()
    vehicle.fail_movement = True
    await svc.give_controls("exec-1")
    assert execution.flight_activity == FlightActivity.HOLDING
    assert vehicle.flight_mode == "BRAKE"


@pytest.mark.asyncio
async def test_stop_attempts_rtl_even_when_neutral_send_fails():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    await svc.open_manual_channel()
    vehicle.fail_movement = True
    await svc.stop("exec-1")
    assert execution.state == MissionState.RETURNING_HOME
    assert vehicle.flight_mode == "RTL"


@pytest.mark.asyncio
async def test_camera_loss_from_manual_with_stale_telemetry_still_requests_rtl():
    svc, hub, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    await svc.open_manual_channel()
    await hub.publish(sample(age_s=12))
    assert await svc.interrupt_due_to_camera_loss()
    assert execution.state == MissionState.RETURNING_HOME
    assert vehicle.flight_mode == "RTL"


@pytest.mark.asyncio
async def test_take_from_paused_operator_hold_records_departure_anchor():
    svc, _, vehicle, _, execution = await service_with_running()
    execution.state = MissionState.PAUSED
    execution.flight_activity = FlightActivity.HOLDING
    execution.hold_reason = HoldReason.OPERATOR_PAUSE
    svc._state = MissionState.PAUSED
    vehicle.flight_mode = "BRAKE"
    await svc.take_controls("exec-1")
    assert svc._route_departure_anchor is not None
    assert svc._route_departure_anchor.unfinished_target == execution.active_target

@pytest.mark.asyncio
async def test_give_cannot_override_native_rtl_and_does_not_claim_holding():
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    await svc.open_manual_channel()
    vehicle.flight_mode = "RTL"
    before = len(vehicle.commands)
    with pytest.raises(MissionExecutionError):
        await svc.give_controls("exec-1")
    assert vehicle.flight_mode == "RTL"
    assert len(vehicle.commands) == before
    assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
    assert not execution.manual_control_ready


@pytest.mark.asyncio
async def test_monitor_recognizes_autopilot_rtl_without_issuing_guidance():
    svc, hub, vehicle, events, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    queue, _ = await hub.subscribe()
    task = asyncio.create_task(svc._monitor_execution("exec-1", queue))
    svc._execution_monitor_task = task
    try:
        vehicle.flight_mode = "RTL"
        await hub.publish(sample())
        for _ in range(100):
            if execution.state == MissionState.RETURNING_HOME:
                break
            await asyncio.sleep(0.005)
        assert execution.state == MissionState.RETURNING_HOME
        assert execution.flight_activity is None
        assert execution.manual_control_ready is False
        assert events.subscribe()[1].state == MissionState.RETURNING_HOME
        assert (await svc.manual_channel_input(epoch, input_frame(epoch)))["revoked"]
        assert vehicle.flight_mode == "RTL"
        assert ("hold",) == vehicle.commands[0]
        assert not any(cmd[0] == "rtl" for cmd in vehicle.commands)
    finally:
        await svc.shutdown()

@pytest.mark.asyncio
async def test_extended_idle_stream_does_not_accidentally_trigger_rtl(monkeypatch):
    import app.mission.mission_service as module
    t = [50.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    for sequence in range(1, 4202):  # 420 seconds of 10 Hz zero input
        outcome = await svc.manual_channel_input(epoch, input_frame(epoch, sequence))
        assert outcome["accepted"] is True
        t[0] += 0.1
        if sequence % 5 == 0:
            assert await svc.manual_watchdog_tick(epoch)
    assert vehicle.flight_mode == "GUIDED"
    assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
    assert ("rtl",) not in vehicle.commands
    await svc.close_manual_channel(epoch)
    assert vehicle.flight_mode == "BRAKE"


@pytest.mark.asyncio
async def test_diagonal_movement_respects_total_speed_and_combines_climb_yaw():
    import math
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1, right=1, up=1, yaw=-1))
    _, forward, right, up, yaw = vehicle.commands[-1]
    assert math.hypot(forward, right) == pytest.approx(2.0)
    assert up == 0.75
    assert yaw == -20.0


@pytest.mark.asyncio
async def test_concurrent_stop_and_movement_never_sends_velocity_after_rtl():
    for _ in range(30):
        svc, _, vehicle, _, execution = await service_with_running()
        await svc.take_controls("exec-1")
        epoch = (await svc.open_manual_channel())["controlEpoch"]
        await svc.manual_channel_input(epoch, input_frame(epoch, 1))
        await asyncio.gather(
            svc.stop("exec-1"),
            svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1)),
        )
        rtl_index = vehicle.commands.index(("rtl",))
        assert all(command[0] != "velocity" for command in vehicle.commands[rtl_index + 1:])
        assert execution.state == MissionState.RETURNING_HOME


@pytest.mark.asyncio
async def test_several_short_input_gaps_neutralize_without_mode_switching(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    ready = await svc.open_manual_channel()
    epoch = ready["controlEpoch"]
    assert ready["watchdogMs"] == 450
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1)))["accepted"]
    for cycle in range(10):
        t[0] += 0.8  # Worse than frontend's normal 100 ms cadence.
        assert await svc.manual_watchdog_tick(epoch)
        assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
        # W must not resume after a silence without an explicit released frame.
        assert (await svc.manual_channel_input(
            epoch, input_frame(epoch, cycle * 3 + 3, forward=1)))['accepted']
        assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
        await svc.manual_channel_input(epoch, input_frame(epoch, cycle * 3 + 4))
        assert (await svc.manual_channel_input(
            epoch, input_frame(epoch, cycle * 3 + 5, forward=1)))['accepted']
        assert vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)
        t[0] += 0.1
    assert vehicle.flight_mode == "GUIDED"
    assert execution.manual_control_ready
    assert sum(cmd[0] == "hold" for cmd in vehicle.commands) == 1  # Take only.
    assert sum(cmd[0] == "set_guided" for cmd in vehicle.commands) == 1
    await svc.give_controls("exec-1")
    assert sum(cmd[0] == "hold" for cmd in vehicle.commands) == 2  # Give.


@pytest.mark.asyncio
async def test_late_packet_before_next_watchdog_tick_must_release_keys(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    await svc.manual_channel_input(epoch, input_frame(epoch, 2, forward=1))
    t[0] += 0.6
    # No watchdog tick occurs before this delayed packet.
    outcome = await svc.manual_channel_input(epoch, input_frame(epoch, 3, forward=1))
    assert outcome['accepted']
    assert vehicle.commands[-1] == ("velocity", 0.0, 0.0, 0.0, 0.0)
    await svc.manual_channel_input(epoch, input_frame(epoch, 4))
    await svc.manual_channel_input(epoch, input_frame(epoch, 5, forward=1))
    assert vehicle.commands[-1] == ("velocity", 2.0, 0.0, 0.0, 0.0)
    assert vehicle.flight_mode == "GUIDED"


@pytest.mark.asyncio
async def test_no_initial_input_does_not_brake_after_only_half_second(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, execution = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    t[0] += 0.6
    assert await svc.manual_watchdog_tick(epoch)
    assert vehicle.flight_mode == "GUIDED"
    assert execution.manual_control_ready
    t[0] += 2.5
    assert not await svc.manual_watchdog_tick(epoch)
    assert vehicle.flight_mode == "BRAKE"
    assert not execution.manual_control_ready


@pytest.mark.asyncio
async def test_invalid_packets_cannot_keep_abandoned_socket_alive(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    for seq in range(2, 10):
        t[0] += 0.4
        malformed = input_frame(epoch, seq, forward=99)
        assert not (await svc.manual_channel_input(epoch, malformed))["accepted"]
        still_open = await svc.manual_watchdog_tick(epoch)
        if seq < 9:
            assert still_open
    assert not still_open
    assert vehicle.flight_mode == "BRAKE"
    assert (await svc.manual_channel_input(epoch, input_frame(epoch, 10)))["revoked"]


@pytest.mark.asyncio
async def test_socket_disconnect_still_brakes_immediately_after_neutral_gap(monkeypatch):
    import app.mission.mission_service as module
    t = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: t[0])
    svc, _, vehicle, _, _ = await service_with_running()
    await svc.take_controls("exec-1")
    epoch = (await svc.open_manual_channel())["controlEpoch"]
    await svc.manual_channel_input(epoch, input_frame(epoch, 1))
    t[0] += 0.7
    assert await svc.manual_watchdog_tick(epoch)
    await svc.close_manual_channel(epoch)
    assert vehicle.flight_mode == "BRAKE"
    assert sum(cmd[0] == "hold" for cmd in vehicle.commands) == 2
