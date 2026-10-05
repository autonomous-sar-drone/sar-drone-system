from types import SimpleNamespace
from datetime import timezone

import asyncio
import pytest

from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import (normalize_global_position_int, run_mavlink_telemetry)


def test_normalize_global_position_int() -> None:
    message = SimpleNamespace(
        lat=300000000,
        lon=-970000000,
        relative_alt=25000,
        hdg=9000,
        vx=300,
        vy=400
    )

    sample = normalize_global_position_int(message, 1)

    assert sample.sequence == 1
    assert sample.timestamp_utc.tzinfo is timezone.utc
    assert sample.latitude_deg == 30.0
    assert sample.longitude_deg == -97.0
    assert sample.relative_altitude_m == 25.0
    assert sample.heading_deg == 90.0
    assert sample.ground_speed_mps == 5.0


def test_normalize_global_position_int_unknown_heading() -> None:
    message = SimpleNamespace(
        lat=300000000,
        lon=-970000000,
        relative_alt=25000,
        hdg=65535,
        vx=300,
        vy=400
    )

    sample = normalize_global_position_int(message, 1)

    assert sample.sequence == 1
    assert sample.timestamp_utc.tzinfo is timezone.utc
    assert sample.latitude_deg == 30.0
    assert sample.longitude_deg == -97.0
    assert sample.relative_altitude_m == 25.0
    assert sample.heading_deg is None
    assert sample.ground_speed_mps == 5.0


class FakeConnection:
    def __init__(self, message: SimpleNamespace) -> None:
        self._message = message
        self._message_sent = False

    def recv_match(self, type: str, blocking: bool) -> SimpleNamespace | None:
        #send one fake MAVLink message, then act like nothing else is available
        if not self._message_sent:
            self._message_sent = True
            return self._message

        return None


@pytest.mark.asyncio
async def test_run_mavlink_telemetry_publishes_sample() -> None:
    message = SimpleNamespace(
        lat=300000000,
        lon=-970000000,
        relative_alt=25000,
        hdg=9000,
        vx=300,
        vy=400
    )

    hub = TelemetryHub()
    connection = FakeConnection(message)

    #start the telemetry stream in the background
    telemetry_task = asyncio.create_task(run_mavlink_telemetry(hub, connection))

    try:
        #wait for the fake MAVLink message to make it through to the hub
        version, sample = await asyncio.wait_for(hub.wait_for_update(0), timeout=1.0)

        assert version == 1
        assert sample is not None
        assert sample.sequence == 1
        assert sample.latitude_deg == 30.0
        assert sample.longitude_deg == -97.0
        assert sample.relative_altitude_m == 25.0
        assert sample.heading_deg == 90.0
        assert sample.ground_speed_mps == 5.0

    finally:
        #stop the infinite telemetry loop once the test is done
        telemetry_task.cancel()

        try:
            await telemetry_task
        except asyncio.CancelledError:
            pass