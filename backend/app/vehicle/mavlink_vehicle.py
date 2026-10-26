import asyncio
from datetime import datetime, timezone
from typing import Any

from pymavlink import mavutil

from app.domain.models.route import AltitudeReference, Waypoint
from app.domain.models.vehicle_status import VehicleStatus
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError
from app.vehicle.mavlink_telemetry import normalize_global_position_int


# the position target message has several optional controls
# this mask uses only latitude, longitude, and altitude
POSITION_ONLY_TYPE_MASK = 0b110111111000


def _get_mavlink() -> Any:
    # pymavlink can mark the MAVLink constants as optional
    # check once here before using them in vehicle commands
    mavlink = mavutil.mavlink

    if mavlink is None:
        raise RuntimeError("MAVLink dialect was not initialized")

    return mavlink


class MavlinkVehicle:
    def __init__(
        self,
        connection: Any,
        telemetry_hub: TelemetryHub,
        heartbeat_timeout_s: float = 3.0,
        command_timeout_s: float = 5.0,
    ) -> None:
        self._connection = connection
        self._telemetry_hub = telemetry_hub
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._command_timeout_s = command_timeout_s

        # telemetry sequences are local to this backend, not MAVLink packet numbers
        self._telemetry_sequence = 0

        # aircraft heartbeat data is used for mode and armed status
        self._armed = False
        self._flight_mode: str | None = None
        self._last_heartbeat_utc: datetime | None = None

        # command waits wake up when a heartbeat reports a mode or arm change
        self._status_condition = asyncio.Condition()

        # one command at a time avoids overlapping aircraft mode transitions
        self._command_lock = asyncio.Lock()

        # ACKs arrive on the shared receiver and are routed to the waiting command
        self._pending_command_acks: dict[int, asyncio.Queue[Any]] = {}

        self._receive_task: asyncio.Task[None] | None = None
        self._stop_requested = False

    def get_status(self) -> VehicleStatus:
        connected = False

        if self._last_heartbeat_utc is not None:
            heartbeat_age_s = (
                datetime.now(timezone.utc) - self._last_heartbeat_utc
            ).total_seconds()

            # connected depends on a recent aircraft heartbeat, not just one at startup
            connected = heartbeat_age_s <= self._heartbeat_timeout_s

        return VehicleStatus(
            connected=connected,
            armed=self._armed,
            flight_mode=self._flight_mode,
            last_heartbeat_utc=self._last_heartbeat_utc,
        )

    async def start(self, telemetry_rate_hz: float = 2.0) -> None:
        if self._receive_task is not None:
            return

        # before the receiver starts, the initial heartbeat is read directly
        heartbeat = await asyncio.to_thread(
            self._connection.wait_heartbeat,
            timeout=self._heartbeat_timeout_s,
        )

        if heartbeat is None:
            raise TimeoutError("No vehicle heartbeat received")

        await self._handle_heartbeat(heartbeat)

        # after startup the receiver is the only code calling recv_match
        self._stop_requested = False
        self._receive_task = asyncio.create_task(self._receive_loop())

        try:
            await self._request_global_position_interval(
                telemetry_rate_hz
            )
        except Exception:
            # close the receiver and connection if startup fails partway through
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stop_requested = True

        try:
            if self._receive_task is not None:
                try:
                    # the short receive timeout lets shutdown exit normally
                    await self._receive_task
                finally:
                    self._receive_task = None
        finally:
            # close the connection even if the receiver task raised an error
            self._connection.close()

    async def set_guided(self) -> None:
        await self._set_mode_and_wait("GUIDED")

    async def arm(self) -> None:
        self._ensure_connected()

        mavlink = _get_mavlink()

        await self._send_command_long_and_wait(
            mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            (
                1.0,  # param1: 1 arms, 0 disarms
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ),
        )

        # an accepted command is not the same as a confirmed vehicle mode or state
        await self._wait_for_armed(True)

    async def takeoff(self, altitude_m: float) -> None:
        self._ensure_connected()

        if altitude_m <= 0:
            raise ValueError(
                "Takeoff altitude must be greater than zero"
            )

        # takeoff itself requires GUIDED and armed, regardless of mission flow
        if self._flight_mode != "GUIDED":
            raise VehicleCommandError(
                "Vehicle must be in GUIDED mode before takeoff"
            )

        if not self._armed:
            raise VehicleCommandError(
                "Vehicle must be armed before takeoff"
            )

        mavlink = _get_mavlink()

        await self._send_command_long_and_wait(
            mavlink.MAV_CMD_NAV_TAKEOFF,
            (
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                altitude_m,  # param7: target takeoff altitude
            ),
        )

        # takeoff ACK means the command was accepted, not that altitude was reached

    async def go_to(self, target: Waypoint) -> None:
        self._ensure_connected()

        if (
            target.altitude_reference
            != AltitudeReference.HOME_RELATIVE
        ):
            raise VehicleCommandError(
                "MAVLink vehicle currently supports "
                "HOME_RELATIVE targets only"
            )

        if self._flight_mode != "GUIDED":
            raise VehicleCommandError(
                "Vehicle must be in GUIDED mode "
                "before sending a target"
            )

        mavlink = _get_mavlink()

        # send the GUIDED position target, arrival is checked by MissionService
        self._connection.mav.set_position_target_global_int_send(
            0,  # boot-time field is unused for this command
            self._connection.target_system,
            self._connection.target_component,
            mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            POSITION_ONLY_TYPE_MASK,
            int(round(target.latitude_deg * 1e7)),
            int(round(target.longitude_deg * 1e7)),
            target.altitude_m,
            0.0,  # velocity fields ignored by type mask
            0.0,
            0.0,
            0.0,  # acceleration fields ignored by type mask
            0.0,
            0.0,
            0.0,  # yaw ignored by type mask
            0.0,  # yaw rate ignored by type mask
        )

    async def hold(self) -> None:
        # BRAKE holds position without needing pilot throttle input
        # wait for the heartbeat to confirm the mode actually changed
        await self._set_mode_and_wait("BRAKE")

    async def return_to_launch(self) -> None:
        # RTL is confirmed by heartbeat, not just by requesting the mode
        await self._set_mode_and_wait("RTL")

    async def _receive_loop(self) -> None:
        while not self._stop_requested:
            # one runtime reader handles telemetry, heartbeats, and command ACKs
            # multiple readers could grab messages meant for each other
            message = await asyncio.to_thread(
                self._connection.recv_match,
                blocking=True,
                timeout=0.5,
            )

            if message is None:
                continue

            await self._dispatch_message(message)

    async def _dispatch_message(self, message: Any) -> None:
        message_type = message.get_type()

        if message_type == "GLOBAL_POSITION_INT":
            self._telemetry_sequence += 1

            sample = normalize_global_position_int(
                message,
                self._telemetry_sequence,
            )

            # publish position in the shared format used by the rest of the backend
            await self._telemetry_hub.publish(sample)
            return

        if message_type == "HEARTBEAT":
            await self._handle_heartbeat(message)
            return

        if message_type == "COMMAND_ACK":
            self._handle_command_ack(message)

    async def _handle_heartbeat(self, message: Any) -> None:
        mavlink = _get_mavlink()

        # forwarded streams can include MAVProxy and other component heartbeats
        # only the autopilot heartbeat should determine armed status and mode
        if (
            message.get_srcSystem() != self._connection.target_system
            or message.get_srcComponent() != mavlink.MAV_COMP_ID_AUTOPILOT1
        ):
            return

        armed = bool(
            message.base_mode
            & mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )

        flight_mode = mavutil.mode_string_v10(message)

        async with self._status_condition:
            self._armed = armed
            self._flight_mode = flight_mode
            self._last_heartbeat_utc = datetime.now(timezone.utc)

            # notify commands waiting for an arm or mode change
            self._status_condition.notify_all()

    def _handle_command_ack(self, message: Any) -> None:
        command = int(message.command)
        queue = self._pending_command_acks.get(command)

        if queue is not None:
            # dispatch ACKs to the command waiting for this ID
            queue.put_nowait(message)

    async def _request_global_position_interval(
        self,
        rate_hz: float,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError(
                "Telemetry rate must be greater than zero"
            )

        mavlink = _get_mavlink()

        # this MAVLink command expects the interval in microseconds
        interval_us = int(1_000_000 / rate_hz)

        await self._send_command_long_and_wait(
            mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            (
                float(
                    mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT
                ),
                float(interval_us),
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ),
        )

    async def _send_command_long_and_wait(
        self,
        command: int,
        params: tuple[
            float,
            float,
            float,
            float,
            float,
            float,
            float,
        ],
    ) -> Any:
        self._ensure_connected()

        mavlink = _get_mavlink()

        async with self._command_lock:
            ack_queue: asyncio.Queue[Any] = asyncio.Queue()

            # register the waiter first, a fast ACK could arrive immediately
            self._pending_command_acks[command] = ack_queue

            try:
                self._connection.mav.command_long_send(
                    self._connection.target_system,
                    self._connection.target_component,
                    command,
                    0,  # command confirmation field
                    *params,
                )

                # use the monotonic event-loop clock for command timeouts
                loop = asyncio.get_running_loop()
                deadline = (
                    loop.time() + self._command_timeout_s
                )

                while True:
                    remaining_s = deadline - loop.time()

                    if remaining_s <= 0:
                        raise VehicleCommandError(
                            "Timed out waiting for ACK "
                            f"for MAVLink command {command}"
                        )

                    try:
                        ack = await asyncio.wait_for(
                            ack_queue.get(),
                            timeout=remaining_s,
                        )
                    except TimeoutError as exc:
                        raise VehicleCommandError(
                            "Timed out waiting for ACK "
                            f"for MAVLink command {command}"
                        ) from exc

                    # IN_PROGRESS is not final, a later ACK can still succeed or fail
                    if (
                        ack.result
                        == mavlink.MAV_RESULT_IN_PROGRESS
                    ):
                        continue

                    if (
                        ack.result
                        != mavlink.MAV_RESULT_ACCEPTED
                    ):
                        raise VehicleCommandError(
                            f"MAVLink command {command} "
                            "was rejected with result "
                            f"{ack.result}"
                        )

                    return ack

            finally:
                # remove the waiter after any outcome, including timeout or cancellation
                self._pending_command_acks.pop(
                    command,
                    None,
                )

    async def _set_mode_and_wait(
        self,
        mode_name: str,
    ) -> None:
        self._ensure_connected()

        if self._flight_mode == mode_name:
            return

        mode_mapping = self._connection.mode_mapping()

        if (
            not mode_mapping
            or mode_name not in mode_mapping
        ):
            raise VehicleCommandError(
                f"Vehicle does not provide flight mode "
                f"{mode_name}"
            )

        async with self._command_lock:
            self._connection.set_mode(
                mode_mapping[mode_name]
            )

            # set_mode sends the request, heartbeat confirms the actual mode
            await self._wait_for_mode(mode_name)

    async def _wait_for_mode(
        self,
        mode_name: str,
    ) -> None:
        async def wait_for_mode_change() -> None:
            async with self._status_condition:
                await self._status_condition.wait_for(
                    lambda: self._flight_mode == mode_name
                )

        try:
            await asyncio.wait_for(
                wait_for_mode_change(),
                timeout=self._command_timeout_s,
            )
        except TimeoutError as exc:
            raise VehicleCommandError(
                f"Vehicle did not enter {mode_name} mode"
            ) from exc

    async def _wait_for_armed(
        self,
        armed: bool,
    ) -> None:
        async def wait_for_armed_change() -> None:
            async with self._status_condition:
                await self._status_condition.wait_for(
                    lambda: self._armed == armed
                )

        try:
            await asyncio.wait_for(
                wait_for_armed_change(),
                timeout=self._command_timeout_s,
            )
        except TimeoutError as exc:
            expected_state = (
                "armed" if armed else "disarmed"
            )

            raise VehicleCommandError(
                f"Vehicle did not become {expected_state}"
            ) from exc

    # reject new commands if no recent aircraft heartbeat is available
    def _ensure_connected(self) -> None:
        if not self.get_status().connected:
            raise VehicleCommandError(
                "Vehicle heartbeat is unavailable or stale"
            )