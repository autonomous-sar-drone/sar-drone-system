import asyncio
from typing import Any

import pytest
from pymavlink import mavutil

from app.domain.models.route import (
    AltitudeReference,
    Waypoint,
)
from app.vehicle.mavlink_vehicle import (
    MavlinkVehicle,
    POSITION_ONLY_TYPE_MASK,
    VehicleCommandError,
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
        self.position_target_calls: list[
            tuple[Any, ...]
        ] = []

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
    ) -> None:
        self.mode_name = mode_name

        if armed:
            self.base_mode = (
                MAVLINK.MAV_MODE_FLAG_SAFETY_ARMED
            )
        else:
            self.base_mode = 0

    def get_type(self) -> str:
        return "HEARTBEAT"


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
    # Real mode_string_v10 understands actual MAVLink heartbeat fields.
    # Tests use a simpler explicit mode_name property.
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

    # An accepted command is not enough. arm() remains pending until a
    # heartbeat confirms that the vehicle actually reports itself armed.
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

    # Armed alone is not sufficient if Copter is not in GUIDED.
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

    # GUIDED alone is also not sufficient before the vehicle is armed.
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

    # command_long_send stores target system/component, command,
    # confirmation, and then MAVLink params 1-7. Altitude is param7.
    assert command_call[10] == pytest.approx(25.0)

    await vehicle._dispatch_message(
        FakeCommandAck(
            MAVLINK.MAV_CMD_NAV_TAKEOFF,
            MAVLINK.MAV_RESULT_ACCEPTED,
        )
    )

    await takeoff_task


@pytest.mark.asyncio
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
async def test_hold_waits_for_loiter_confirmation() -> None:
    vehicle, connection, _ = make_vehicle()

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="GUIDED",
            armed=True,
        )
    )

    hold_task = asyncio.create_task(vehicle.hold())

    await asyncio.sleep(0)

    assert connection.set_mode_calls == [5]

    # The request alone should not count as a successful transition.
    assert hold_task.done() is False

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="LOITER",
            armed=True,
        )
    )

    await hold_task

    assert vehicle.get_status().flight_mode == "LOITER"


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

    # Mission code must not treat the vehicle as returning home just because
    # the RTL request was sent.
    assert rtl_task.done() is False

    await vehicle._dispatch_message(
        FakeHeartbeat(
            mode_name="RTL",
            armed=True,
        )
    )

    await rtl_task

    assert vehicle.get_status().flight_mode == "RTL"