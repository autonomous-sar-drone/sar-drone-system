import asyncio
import math
from typing import Any

import pytest
from pymavlink import mavutil

from app.domain.models.route import (
    AltitudeReference,
    Waypoint,
)
from app.vehicle.errors import VehicleCommandError
from app.vehicle.mavlink_vehicle import (
    MavlinkVehicle,
    POSITION_ONLY_TYPE_MASK,
    POSITION_YAW_TYPE_MASK,
    VELOCITY_YAW_RATE_TYPE_MASK,
)


MAVLINK: Any = mavutil.mavlink

if MAVLINK is None:
    raise RuntimeError("MAVLink dialect was not initialized")


class FakeTelemetryHub:
    def __init__(self) -> None:
        self.samples: list[Any] = []

    async def publish(self, sample: Any) -> None:
        self.samples.append(sample)


class FakeMav:
    def __init__(self) -> None:
        self.command_long_calls: list[tuple[Any, ...]] = []
        self.param_set_calls: list[tuple[Any, ...]] = []
        self.param_read_calls: list[tuple[Any, ...]] = []
        self.position_target_calls: list[
            tuple[Any, ...]
        ] = []
        self.local_target_calls: list[tuple[Any, ...]] = []

    def param_set_send(self, *args: Any) -> None:
        self.param_set_calls.append(args)

    def param_request_read_send(self, *args: Any) -> None:
        self.param_read_calls.append(args)

    def command_long_send(
        self,
        *args: Any,
    ) -> None:
        self.command_long_calls.append(args)

    def set_position_target_global_int_send(
        self,
        *args: Any,
    ) -> None:
        self.position_target_calls.append(args)

    def set_position_target_local_ned_send(self, *args: Any) -> None:
        self.local_target_calls.append(args)


class FakeConnection:
    def __init__(self) -> None:
        self.target_system = 1
        self.target_component = 1

        self.mav = FakeMav()

        self.set_mode_calls: list[int] = []
        self.closed = False

    def mode_mapping(self) -> dict[str, int]:
        return {
            "GUIDED": 4,
            "LOITER": 5,
            "RTL": 6,
            "BRAKE": 17,
        }

    def set_mode(self, mode_id: int) -> None:
        self.set_mode_calls.append(mode_id)

    def close(self) -> None:
        self.closed = True


class FakeHeartbeat:
    def __init__(
        self,
        mode_name: str,
        armed: bool = False,
        src_system: int = 1,
        src_component: int = 1,
    ) -> None:
        self.mode_name = mode_name
        self.src_system = src_system
        self.src_component = src_component

        if armed:
            self.base_mode = (
                MAVLINK.MAV_MODE_FLAG_SAFETY_ARMED
            )
        else:
            self.base_mode = 0

    def get_type(self) -> str:
        return "HEARTBEAT"

    def get_srcSystem(self) -> int:
        return self.src_system

    def get_srcComponent(self) -> int:
        return self.src_component


class FakeCommandAck:
    def __init__(
        self,
        command: int,
        result: int,
    ) -> None:
        self.command = command
        self.result = result

    def get_type(self) -> str:
        return "COMMAND_ACK"


class FakeGlobalPositionInt:
    def __init__(self) -> None:
        self.lat = 302672000
        self.lon = -977431000
        self.relative_alt = 25000
        self.hdg = 9000
        self.vx = 300
        self.vy = 400

    def get_type(self) -> str:
        return "GLOBAL_POSITION_INT"


@pytest.fixture(autouse=True)
def fake_mode_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fake heartbeats use mode_name instead of parsing full MAVLink mode bits
    monkeypatch.setattr(
        mavutil,
        "mode_string_v10",
        lambda message: message.mode_name,
    )


def make_vehicle() -> tuple[
    MavlinkVehicle,
    FakeConnection,
    FakeTelemetryHub,
]:
    connection = FakeConnection()
    hub = FakeTelemetryHub()

    vehicle = MavlinkVehicle(
        connection=connection,
        telemetry_hub=hub,  # type: ignore[arg-type]
        heartbeat_timeout_s=3.0,
        command_timeout_s=0.25,
    )

    return vehicle, connection, hub


@pytest.mark.asyncio
async def test_heartbeat_updates_vehicle_status() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    status = vehicle.get_status()

    assert status.connected is True
    assert status.armed is True
    assert status.flight_mode == "GUIDED"
    assert status.last_heartbeat_utc is not None


@pytest.mark.asyncio
async def test_ground_station_heartbeat_does_not_fake_a_disarm() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    aircraft_heartbeat_time = vehicle.get_status().last_heartbeat_utc

    # forwarded MAVProxy heartbeats may look disarmed, but are not the vehicle
    await vehicle._dispatch_message(
        FakeHeartbeat("UNKNOWN", armed=False, src_system=255, src_component=230)
    )

    status = vehicle.get_status()
    assert status.armed is True
    assert status.flight_mode == "GUIDED"
    assert status.last_heartbeat_utc == aircraft_heartbeat_time


@pytest.mark.asyncio
# non-autopilot components from the same system should not override aircraft mode
async def test_other_component_heartbeat_does_not_override_autopilot() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    await vehicle._dispatch_message(
        FakeHeartbeat("UNKNOWN", armed=False, src_system=1, src_component=100)
    )

    assert vehicle.get_status().armed is True
    assert vehicle.get_status().flight_mode == "GUIDED"


@pytest.mark.asyncio
async def test_real_autopilot_disarm_is_still_detected() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=False))

    assert vehicle.get_status().armed is False


@pytest.mark.asyncio
# checks MAVLink positions reach the telemetry hub in the expected units
async def test_global_position_is_published_as_telemetry() -> None:
    vehicle, _, hub = make_vehicle()

    await vehicle._dispatch_message(
        FakeGlobalPositionInt()
    )

    assert len(hub.samples) == 1

    sample = hub.samples[0]

    assert sample.sequence == 1
    assert sample.latitude_deg == pytest.approx(
        30.2672
    )
    assert sample.longitude_deg == pytest.approx(
        -97.7431
    )
    assert sample.relative_altitude_m == pytest.approx(
        25.0
    )
    assert sample.heading_deg == pytest.approx(90.0)
    assert sample.ground_speed_mps == pytest.approx(
        5.0
    )


@pytest.mark.asyncio
async def test_arm_waits_for_ack_and_armed_heartbeat() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=False,
        )
    )

    arm_task = asyncio.create_task(vehicle.arm())

    await asyncio.sleep(0)

    assert len(
        connection.mav.command_long_calls
    ) == 1

    command_call = (
        connection.mav.command_long_calls[0]
    )

    assert (
        command_call[2]
        == MAVLINK.MAV_CMD_COMPONENT_ARM_DISARM
    )

    await vehicle._dispatch_message(
        FakeCommandAck(
            MAVLINK.MAV_CMD_COMPONENT_ARM_DISARM,
            MAVLINK.MAV_RESULT_ACCEPTED,
        )
    )

    await asyncio.sleep(0)

    # the ACK alone does not finish arming, the heartbeat has to show armed
    assert arm_task.done() is False

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    await arm_task

    assert vehicle.get_status().armed is True


@pytest.mark.asyncio
async def test_rejected_command_raises_vehicle_error() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=False,
        )
    )

    arm_task = asyncio.create_task(vehicle.arm())

    await asyncio.sleep(0)

    await vehicle._dispatch_message(
        FakeCommandAck(
            MAVLINK.MAV_CMD_COMPONENT_ARM_DISARM,
            MAVLINK.MAV_RESULT_DENIED,
        )
    )

    with pytest.raises(
        VehicleCommandError,
        match="was rejected",
    ):
        await arm_task


@pytest.mark.asyncio
async def test_takeoff_requires_guided_and_armed_vehicle() -> None:
    vehicle, _, _ = make_vehicle()

    # takeoff needs both the right mode and armed status
    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="LOITER",
            armed=True,
        )
    )

    with pytest.raises(
        VehicleCommandError,
        match="GUIDED mode",
    ):
        await vehicle.takeoff(25.0)

    # GUIDED without arming is still not ready for takeoff
    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=False,
        )
    )

    with pytest.raises(
        VehicleCommandError,
        match="armed",
    ):
        await vehicle.takeoff(25.0)


@pytest.mark.asyncio
async def test_takeoff_sends_command_when_vehicle_is_ready() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    takeoff_task = asyncio.create_task(
        vehicle.takeoff(25.0)
    )

    await asyncio.sleep(0)

    assert len(
        connection.mav.command_long_calls
    ) == 1

    command_call = (
        connection.mav.command_long_calls[0]
    )

    assert (
        command_call[2]
        == MAVLINK.MAV_CMD_NAV_TAKEOFF
    )

    # MAVLink takeoff altitude is param7 in the command_long message
    assert command_call[10] == pytest.approx(25.0)

    await vehicle._dispatch_message(
        FakeCommandAck(
            MAVLINK.MAV_CMD_NAV_TAKEOFF,
            MAVLINK.MAV_RESULT_ACCEPTED,
        )
    )

    await takeoff_task


@pytest.mark.asyncio
# waypoint commands should send only position, not velocity or yaw targets
async def test_go_to_sends_guided_position_target() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    target = Waypoint(
        latitude_deg=30.2674,
        longitude_deg=-97.7430,
        altitude_m=25.0,
        altitude_reference=(
            AltitudeReference.HOME_RELATIVE
        ),
    )

    await vehicle.go_to(target)

    assert len(
        connection.mav.position_target_calls
    ) == 1

    call = connection.mav.position_target_calls[0]

    assert (
        call[3]
        == MAVLINK.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
    )
    assert call[4] == POSITION_ONLY_TYPE_MASK
    assert call[5] == 302674000
    assert call[6] == -977430000
    assert call[7] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_viewpoint_heading_uses_absolute_yaw_with_position_hold() -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    target = Waypoint(30.2674, -97.7430, 18.0, AltitudeReference.HOME_RELATIVE)
    await vehicle.point_at_viewpoint(target, math.radians(359))
    assert len(connection.mav.position_target_calls) == 1
    call = connection.mav.position_target_calls[0]
    assert call[3] == MAVLINK.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
    assert call[4] == POSITION_YAW_TYPE_MASK
    assert call[4] & (1 << 10) == 0  # yaw supplied, not ignored
    assert call[4] & (1 << 11) != 0  # yaw rate ignored
    assert call[5:8] == (302674000, -977430000, 18.0)
    assert call[14] == pytest.approx(math.radians(359))
    assert call[15] == 0.0


@pytest.mark.asyncio
async def test_viewpoint_heading_requires_guided_and_finite_target() -> None:
    vehicle, connection, _ = make_vehicle()
    target = Waypoint(30.2674, -97.7430, 18.0, AltitudeReference.HOME_RELATIVE)
    with pytest.raises(ValueError):
        await vehicle.point_at_viewpoint(target, float('nan'))
    await vehicle._dispatch_message(FakeHeartbeat("BRAKE", armed=True))
    with pytest.raises(VehicleCommandError, match="GUIDED"):
        await vehicle.point_at_viewpoint(target, 0.0)
    assert not connection.mav.position_target_calls


@pytest.mark.asyncio
async def test_go_to_requires_guided_mode() -> None:
    vehicle, _, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="LOITER",
            armed=True,
        )
    )

    target = Waypoint(
        latitude_deg=30.2674,
        longitude_deg=-97.7430,
        altitude_m=25.0,
        altitude_reference=(
            AltitudeReference.HOME_RELATIVE
        ),
    )

    with pytest.raises(
        VehicleCommandError,
        match="GUIDED mode",
    ):
        await vehicle.go_to(target)


@pytest.mark.asyncio
async def test_hold_waits_for_brake_confirmation() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    hold_task = asyncio.create_task(vehicle.hold())

    await asyncio.sleep(0)

    assert connection.set_mode_calls == [17]

    # hold stays pending until the aircraft actually reports BRAKE
    assert hold_task.done() is False
    await vehicle._dispatch_message(FakeHeartbeat("LOITER", armed=True))
    assert hold_task.done() is False

    await vehicle._dispatch_message(
        FakeHeartbeat(mode_name="BRAKE", armed=True)
    )

    await hold_task
    assert vehicle.get_status().flight_mode == "BRAKE"


@pytest.mark.asyncio
async def test_rtl_waits_for_vehicle_mode_confirmation() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    rtl_task = asyncio.create_task(
        vehicle.return_to_launch()
    )

    await asyncio.sleep(0)

    assert connection.set_mode_calls == [6]

    # requesting RTL is not enough, a heartbeat must confirm the mode
    assert rtl_task.done() is False

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="RTL",
            armed=True,
        )
    )

    await rtl_task

    assert vehicle.get_status().flight_mode == "RTL"

class FakeParamValue:
    def __init__(self, name: str, value: float, system: int = 1, component: int = 1):
        self.param_id = name
        self.param_value = value
        self.system = system
        self.component = component

    def get_type(self) -> str:
        return "PARAM_VALUE"

    def get_srcSystem(self) -> int:
        return self.system

    def get_srcComponent(self) -> int:
        return self.component


@pytest.mark.asyncio
async def test_rtl_parameters_are_written_and_read_back_before_flight():
    from datetime import datetime, timezone
    vehicle, connection, _ = make_vehicle()
    vehicle._last_heartbeat_utc = datetime.now(timezone.utc)
    task = asyncio.create_task(vehicle.configure_safe_rtl(50))
    for i, (name, value) in enumerate((
        ("RTL_ALT_M", 50.0),
        ("RTL_ALT_TYPE", 0.0),
        ("RTL_CONE_SLOPE", 0.0),
    )):
        async def wait_for_set():
            while len(connection.mav.param_set_calls) < i + 1:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_set(), 0.5)
        assert connection.mav.param_set_calls[i][2] == name.encode()
        await vehicle._dispatch_message(FakeParamValue(name, value))
        async def wait_for_read():
            while len(connection.mav.param_read_calls) < i + 1:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_read(), 0.5)
        assert connection.mav.param_read_calls[i][2] == name.encode()
        await vehicle._dispatch_message(FakeParamValue(name, value))
    await task
    assert len(connection.mav.param_set_calls) == 3
    assert len(connection.mav.param_read_calls) == 3


@pytest.mark.asyncio
async def test_rtl_configuration_rejects_parameter_disagreement():
    from datetime import datetime, timezone
    vehicle, connection, _ = make_vehicle()
    vehicle._last_heartbeat_utc = datetime.now(timezone.utc)
    task = asyncio.create_task(vehicle.configure_safe_rtl(50))
    async def wait_for_set():
        while not connection.mav.param_set_calls:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_for_set(), 0.5)
    await vehicle._dispatch_message(FakeParamValue("RTL_ALT_M", 15))
    with pytest.raises(VehicleCommandError, match="verification failed"):
        await task
    assert len(connection.mav.param_read_calls) == 0


@pytest.mark.asyncio
async def test_rtl_parameters_are_not_changed_while_armed():
    from datetime import datetime, timezone
    vehicle, connection, _ = make_vehicle()
    vehicle._last_heartbeat_utc = datetime.now(timezone.utc)
    vehicle._armed = True
    with pytest.raises(VehicleCommandError, match="while armed"):
        await vehicle.configure_safe_rtl(50)
    assert connection.mav.param_set_calls == []

@pytest.mark.asyncio
async def test_manual_velocity_sends_body_relative_velocity_and_yaw_rate() -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))

    await vehicle.send_manual_velocity(1.5, -0.75, 0.5, 20.0)

    assert len(connection.mav.local_target_calls) == 1
    call = connection.mav.local_target_calls[0]
    assert call[0:5] == (
        0, 1, 1, MAVLINK.MAV_FRAME_BODY_OFFSET_NED,
        VELOCITY_YAW_RATE_TYPE_MASK,
    )
    assert VELOCITY_YAW_RATE_TYPE_MASK == 1479
    assert call[5:8] == (0.0, 0.0, 0.0)
    assert call[8:11] == pytest.approx((1.5, -0.75, -0.5))
    assert call[11:15] == (0.0, 0.0, 0.0, 0.0)
    assert call[15] == pytest.approx(0.3490658504)
    assert connection.mav.position_target_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((2.0, 0.0, 0.0, 0.0), (2.0, 0.0, 0.0, 0.0)),
        ((-2.0, 0.0, 0.0, 0.0), (-2.0, 0.0, 0.0, 0.0)),
        ((0.0, 2.0, 0.0, 0.0), (0.0, 2.0, 0.0, 0.0)),
        ((0.0, 0.0, -0.75, -20.0), (0.0, 0.0, 0.75, -0.3490658504)),
        ((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)),
        ((1.0, 0.5, 0.75, 10.0), (1.0, 0.5, -0.75, 0.1745329252)),
    ],
)
async def test_manual_velocity_signs_combined_axes_and_neutral(
    values: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
) -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    await vehicle.send_manual_velocity(*values)
    call = connection.mav.local_target_calls[-1]
    assert (call[8], call[9], call[10], call[15]) == pytest.approx(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,armed", [("BRAKE", True), ("RTL", True), ("GUIDED", False)])
async def test_manual_velocity_rejects_ineligible_aircraft(mode: str, armed: bool) -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat(mode, armed=armed))
    with pytest.raises(VehicleCommandError, match="GUIDED|armed"):
        await vehicle.send_manual_velocity(1.0, 0.0, 0.0, 0.0)
    assert connection.mav.local_target_calls == []


@pytest.mark.asyncio
async def test_manual_velocity_rejects_missing_or_stale_heartbeat() -> None:
    from datetime import timedelta
    vehicle, connection, _ = make_vehicle()
    with pytest.raises(VehicleCommandError, match="heartbeat"):
        await vehicle.send_manual_velocity(1.0, 0.0, 0.0, 0.0)
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    assert vehicle._last_heartbeat_utc is not None
    vehicle._last_heartbeat_utc -= timedelta(seconds=10)
    with pytest.raises(VehicleCommandError, match="heartbeat"):
        await vehicle.send_manual_velocity(1.0, 0.0, 0.0, 0.0)
    assert connection.mav.local_target_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_manual_velocity_rejects_nonfinite_input(bad: float) -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    with pytest.raises(ValueError, match="finite"):
        await vehicle.send_manual_velocity(0.0, 0.0, bad, 0.0)
    assert connection.mav.local_target_calls == []


@pytest.mark.asyncio
async def test_manual_velocity_waits_for_mode_transition_lock() -> None:
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("BRAKE", armed=True))
    guided_task = asyncio.create_task(vehicle.set_guided())
    await asyncio.sleep(0)
    assert connection.set_mode_calls == [4]

    movement_task = asyncio.create_task(vehicle.send_manual_velocity(1, 0, 0, 0))
    await asyncio.sleep(0)
    assert connection.mav.local_target_calls == []

    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    await guided_task
    await movement_task
    assert len(connection.mav.local_target_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    (2.01, 0.0, 0.0, 0.0),
    (2.0, 2.0, 0.0, 0.0),
    (0.0, 0.0, 0.751, 0.0),
    (0.0, 0.0, -0.751, 0.0),
    (0.0, 0.0, 0.0, 20.01),
    (0.0, 0.0, 0.0, -20.01),
])
async def test_manual_vehicle_adapter_refuses_commands_above_speed_limits(values):
    vehicle, connection, _ = make_vehicle()
    await vehicle._dispatch_message(FakeHeartbeat("GUIDED", armed=True))
    with pytest.raises(ValueError, match="limits"):
        await vehicle.send_manual_velocity(*values)
    assert connection.mav.local_target_calls == []
