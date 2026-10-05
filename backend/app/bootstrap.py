import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import MAVLINK_ENDPOINT
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import (
    create_mavlink_connection,
    initialize_mavlink_telemetry,
    run_mavlink_telemetry
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    hub = TelemetryHub()
    connection = create_mavlink_connection(MAVLINK_ENDPOINT)

    #make the shared hub available to API routes
    app.state.telemetry_hub = hub

    await initialize_mavlink_telemetry(connection)

    telemetry_task = asyncio.create_task(
        run_mavlink_telemetry(hub, connection)
    )

    try:
        yield

    finally:
        telemetry_task.cancel()

        try:
            await telemetry_task
        except asyncio.CancelledError:
            pass

        connection.close()