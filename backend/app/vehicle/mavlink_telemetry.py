from pymavlink import mavutil
from app.domain.models.telemetry import TelemetrySample
from app.runtime.telemetry_hub import TelemetryHub

import asyncio
from datetime import datetime, timezone
from math import sqrt
from typing import Any


def create_mavlink_connection(endpoint: str) -> Any:
    #create the pymavlink connection using whatever endpoint is provided by config
    return mavutil.mavlink_connection(endpoint)


async def run_mavlink_telemetry(hub: TelemetryHub, connection: Any) -> None:
    #sequence belongs to this running telemetry stream
    sequence = 0

    while True:
        #recv_match is blocking, so run it off the main asyncio event loop
        message = await asyncio.to_thread(
            connection.recv_match,
            type="GLOBAL_POSITION_INT",
            blocking=True
        )

        #nothing to normalize or publish if no message was returned
        if message is None:
            continue

        sequence += 1

        #convert the raw MAVLink message into our application telemetry model
        sample = normalize_global_position_int(message, sequence)

        #publish the newest normalized sample to all TelemetryHub consumers
        await hub.publish(sample)


async def initialize_mavlink_telemetry(connection: Any, rate_hz: float = 2.0) -> None:
    #wait until ArduPilot vehicle is actually present on the connection
    await asyncio.to_thread(connection.wait_heartbeat)

    mavlink = mavutil.mavlink

    if mavlink is None:
        raise RuntimeError("MAVLink dialect was not initialized")

    #MAVLink message intervals are given in microseconds
    interval_us = int(1_000_000 / rate_hz)

    #request GLOBAL_POSITION_INT at the specified rate
    connection.mav.command_long_send(
        connection.target_system,
        connection.target_component,
        mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
        0,
        mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT,
        interval_us,
        0,
        0,
        0,
        0,
        0
    )

    #make sure ArduPilot accepted the message interval request
    response = await asyncio.to_thread(
        connection.recv_match,
        type="COMMAND_ACK",
        blocking=True,
        timeout=5
    )

    if response is None:
        raise TimeoutError("No response to telemetry interval request")

    if (
        response.command != mavlink.MAV_CMD_SET_MESSAGE_INTERVAL
        or response.result != mavlink.MAV_RESULT_ACCEPTED
    ):
        raise RuntimeError("Telemetry interval request was not accepted")


def normalize_global_position_int(message: Any, sequence: int) -> TelemetrySample:
    #65535 is MAVLink's unknown heading value
    if message.hdg == 65535:
        heading_deg = None
    else:
        heading_deg = message.hdg / 100

    #horizontal ground speed from vx and vy, converts cm/s to m/s
    ground_speed_mps = sqrt(message.vx ** 2 + message.vy ** 2) / 100

    #convert MAVLink values into normal application units
    sample = TelemetrySample(
        sequence=sequence,
        timestamp_utc=datetime.now(timezone.utc),
        latitude_deg=message.lat / 1e7,
        longitude_deg=message.lon / 1e7,
        relative_altitude_m=message.relative_alt / 1000,
        heading_deg=heading_deg,
        ground_speed_mps=ground_speed_mps
    )

    return sample