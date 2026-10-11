import asyncio
from datetime import datetime, timezone
from typing import Any

from pymavlink import mavutil

from app.domain.models.route import AltitudeReference, Waypoint
from app.domain.models.vehicle_status import VehicleStatus
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import normalize_global_position_int


# SET_POSITION_TARGET_GLOBAL_INT can control position, velocity, acceleration,
# yaw, and yaw rate. This mask tells ArduPilot to use only lat/lon/altitude.
POSITION_ONLY_TYPE_MASK = 0b110111111000


class VehicleCommandError(RuntimeError):
    pass


def _get_mavlink() -> Any:
    # pymavlink types mavutil.mavlink as possibly None even though it should
    # normally be initialized after the connection is created. Keep the check
    # here so the rest of this module can safely access MAVLink constants.
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

        # Sequence belongs to this running backend telemetry stream rather than
        # the raw MAVLink packet numbering.
        self._telemetry_sequence = 0

        # Heartbeats maintain the latest reported aircraft status.
        self._armed = False
        self._flight_mode: str | None = None
        self._last_heartbeat_utc: datetime | None = None

        # Status waiters use this condition to sleep until a heartbeat reports
        # a relevant state change, such as becoming armed or entering RTL.
        self._status_condition = asyncio.Condition()

        # Commands are serialized for the MVP so mode/command transitions do
        # not overlap and create ambiguous aircraft state.
        self._command_lock = asyncio.Lock()

        # COMMAND_ACK messages arrive through the shared receive loop. A
        # waiting command temporarily registers its own queue under command ID.
        self._pending_command_acks: dict[int, asyncio.Queue[Any]] = {}

        self._receive_task: asyncio.Task[None] | None = None
        self._stop_requested = False

    def get_status(self) -> VehicleStatus:
        connected = False

        if self._last_heartbeat_utc is not None:
            heartbeat_age_s = (
                datetime.now(timezone.utc) - self._last_heartbeat_utc
            ).total_seconds()

            # "Connected" is based on recent evidence from the vehicle rather
            # than a flag that stays true forever after the first heartbeat.
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

        # Before the shared receiver exists, wait_heartbeat can safely read
        # directly from the MAVLink connection.
        heartbeat = await asyncio.to_thread(
            self._connection.wait_heartbeat,
            timeout=self._heartbeat_timeout_s,
        )

        if heartbeat is None:
            raise TimeoutError("No vehicle heartbeat received")

        await self._handle_heartbeat(heartbeat)

        # From this point onward, _receive_loop is the single owner of
        # recv_match(). Other methods receive messages through dispatching.
        self._stop_requested = False
        self._receive_task = asyncio.create_task(self._receive_loop())

        try:
            await self._request_global_position_interval(
                telemetry_rate_hz
            )
        except Exception:
            # Do not leave a background receiver or connection running when
            # startup only partially succeeds.
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stop_requested = True

        try:
            if self._receive_task is not None:
                try:
                    # recv_match has a short timeout, so the loop will notice
                    # _stop_requested and exit without needing forced teardown.
                    await self._receive_task
                finally:
                    self._receive_task = None
        finally:
            # The connection still closes if the receive task itself failed.
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

        # COMMAND_ACK only confirms that ArduPilot accepted the request.
        # Wait for a heartbeat to confirm the resulting vehicle state.
        await self._wait_for_armed(True)

    async def takeoff(self, altitude_m: float) -> None:
        self._ensure_connected()

        if altitude_m <= 0:
            raise ValueError(
                "Takeoff altitude must be greater than zero"
            )

        # These are technical requirements of the vehicle command, not mission
        # workflow policy, so the Vehicle layer protects them directly.
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

        # This method confirms command acceptance only. Mission execution will
        # use telemetry to determine when the aircraft actually reaches altitude.

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

        # This sets the aircraft's current GUIDED target. It does not wait for
        # arrival; MissionService determines target completion from telemetry.
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
        # Pause behavior is implemented as LOITER so the vehicle holds position.
        await self._set_mode_and_wait("LOITER")

    async def return_to_launch(self) -> None:
        # Success means a heartbeat has actually reported RTL, not merely that
        # the backend attempted to request the mode.
        await self._set_mode_and_wait("RTL")

    async def _receive_loop(self) -> None:
        while not self._stop_requested:
            # This is intentionally the only runtime recv_match consumer.
            # Receive every message and dispatch by type instead of creating
            # competing telemetry, heartbeat, and command readers.
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

            # TelemetryHub remains the application-level source of current
            # normalized vehicle position.
            await self._telemetry_hub.publish(sample)
            return

        if message_type == "HEARTBEAT":
            await self._handle_heartbeat(message)
            return

        if message_type == "COMMAND_ACK":
            self._handle_command_ack(message)

    async def _handle_heartbeat(self, message: Any) -> None:
        mavlink = _get_mavlink()

        armed = bool(
            message.base_mode
            & mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )

        flight_mode = mavutil.mode_string_v10(message)

        async with self._status_condition:
            self._armed = armed
            self._flight_mode = flight_mode
            self._last_heartbeat_utc = datetime.now(timezone.utc)

            # Wake any command currently waiting for a reported status change.
            self._status_condition.notify_all()

    def _handle_command_ack(self, message: Any) -> None:
        command = int(message.command)
        queue = self._pending_command_acks.get(command)

        if queue is not None:
            # Queueing keeps the shared receiver independent from whichever
            # coroutine originally sent this command.
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

        # MAV_CMD_SET_MESSAGE_INTERVAL expects the interval in microseconds.
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

            # Register before sending so a fast ACK cannot arrive before the
            # receiver knows which waiter should receive it.
            self._pending_command_acks[command] = ack_queue

            try:
                self._connection.mav.command_long_send(
                    self._connection.target_system,
                    self._connection.target_component,
                    command,
                    0,  # command confirmation field
                    *params,
                )

                # Use the event loop's monotonic clock for timeout accounting.
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

                    # Some MAVLink commands can first report IN_PROGRESS and
                    # later provide a final ACCEPTED or failure result.
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
                # Never leave a stale waiter registered after timeout, failure,
                # cancellation, or successful completion.
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

            # set_mode only requests the transition. The heartbeat provides
            # authoritative confirmation of the vehicle's reported mode.
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

    def _ensure_connected(self) -> None:
        if not self.get_status().connected:
            raise VehicleCommandError(
                "Vehicle heartbeat is unavailable or stale"
            )