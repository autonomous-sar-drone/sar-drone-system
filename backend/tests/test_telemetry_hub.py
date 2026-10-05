import asyncio
from datetime import datetime, timezone

import pytest

from app.domain.models.telemetry import TelemetrySample
from app.runtime.telemetry_hub import TelemetryHub


#helper for making consistent fake telemetry samples throughout the tests
def make_sample(sequence: int) -> TelemetrySample:
    return TelemetrySample(
        sequence=sequence,
        timestamp_utc=datetime.now(timezone.utc),
        latitude_deg=30.2672,
        longitude_deg=-97.7431,
        relative_altitude_m=25.0,
        heading_deg=90.0,
        ground_speed_mps=4.5
    )


@pytest.mark.asyncio
async def test_initial_state() -> None:
    hub = TelemetryHub()

    version, sample = await hub.get_latest()

    #nothing should have been published yet
    assert version == 0
    assert sample is None


@pytest.mark.asyncio
async def test_publish_updates_latest_sample_and_version() -> None:
    hub = TelemetryHub()
    sample1 = make_sample(1)
    sample2 = make_sample(2)

    await hub.publish(sample1)
    version, latest = await hub.get_latest()

    assert version == 1
    assert latest == sample1

    #publishing again should replace latest and increment the version
    await hub.publish(sample2)
    version, latest = await hub.get_latest()

    assert version == 2
    assert latest == sample2


@pytest.mark.asyncio
async def test_waiter_wakes_for_new_update() -> None:
    hub = TelemetryHub()
    sample = make_sample(1)

    #start a consumer waiting for a version newer than 0
    waiter = asyncio.create_task(hub.wait_for_update(0))
    await asyncio.sleep(0)

    #simulate the telemetry producer publishing a new sample
    await hub.publish(sample)

    version, received_sample = await waiter

    assert version == 1
    assert received_sample == sample


@pytest.mark.asyncio
async def test_multiple_waiters_receive_same_update() -> None:
    hub = TelemetryHub()
    sample = make_sample(1)

    #simulate two consumers waiting on the same TelemetryHub
    waiter1 = asyncio.create_task(hub.wait_for_update(0))
    waiter2 = asyncio.create_task(hub.wait_for_update(0))
    await asyncio.sleep(0)

    #one publication should wake both consumers
    await hub.publish(sample)

    result1, result2 = await asyncio.gather(waiter1, waiter2)

    assert result1 == (1, sample)
    assert result2 == (1, sample)