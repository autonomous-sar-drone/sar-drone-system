import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import (
    DEFAULT_LANE_SPACING_M,
    DEFAULT_SEARCH_ALTITUDE_M,
    MAVLINK_ENDPOINT,
    MAX_ROUTE_DISTANCE_M,
    MAX_TELEMETRY_AGE_S,
    MAX_TRANSIT_DISTANCE_M,
)
from app.domain.models.route import PlanningSettings
from app.mission.mission_service import MissionService
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import (
    create_mavlink_connection,
    initialize_mavlink_telemetry,
    run_mavlink_telemetry,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    hub = TelemetryHub()

    mission_service = MissionService(
        telemetry_hub=hub,
        default_planning_settings=PlanningSettings(
            search_altitude_m=DEFAULT_SEARCH_ALTITUDE_M,
            lane_spacing_m=DEFAULT_LANE_SPACING_M,
        ),
        max_telemetry_age_s=MAX_TELEMETRY_AGE_S,
        max_transit_distance_m=MAX_TRANSIT_DISTANCE_M,
        max_route_distance_m=MAX_ROUTE_DISTANCE_M,
    )

    connection = create_mavlink_connection(MAVLINK_ENDPOINT)

    # Shared application services are available to HTTP and WebSocket routes.
    app.state.telemetry_hub = hub
    app.state.mission_service = mission_service

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