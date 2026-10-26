from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import (
    DEFAULT_LANE_SPACING_M,
    DEFAULT_SEARCH_ALTITUDE_M,
    LANDING_ALTITUDE_TOLERANCE_M,
    LANDING_SPEED_TOLERANCE_MPS,
    MAVLINK_ENDPOINT,
    MAX_ROUTE_DISTANCE_M,
    MAX_TELEMETRY_AGE_S,
    MAX_TRANSIT_DISTANCE_M,
    TAKEOFF_ALTITUDE_TOLERANCE_M,
    TAKEOFF_TIMEOUT_S,
    TARGET_ALTITUDE_TOLERANCE_M,
    TARGET_REACHED_TOLERANCE_M,
)
from app.domain.models.route import PlanningSettings
from app.mission.mission_service import MissionService
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import create_mavlink_connection
from app.vehicle.mavlink_vehicle import MavlinkVehicle


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    telemetry_hub = TelemetryHub()
    mission_hub = MissionHub()
    connection = create_mavlink_connection(MAVLINK_ENDPOINT)
    vehicle = MavlinkVehicle(connection=connection, telemetry_hub=telemetry_hub)

    mission_service = MissionService(
        telemetry_hub=telemetry_hub,
        mission_hub=mission_hub,
        vehicle=vehicle,
        default_planning_settings=PlanningSettings(
            search_altitude_m=DEFAULT_SEARCH_ALTITUDE_M,
            lane_spacing_m=DEFAULT_LANE_SPACING_M,
        ),
        max_telemetry_age_s=MAX_TELEMETRY_AGE_S,
        max_transit_distance_m=MAX_TRANSIT_DISTANCE_M,
        max_route_distance_m=MAX_ROUTE_DISTANCE_M,
        target_reached_tolerance_m=TARGET_REACHED_TOLERANCE_M,
        target_altitude_tolerance_m=TARGET_ALTITUDE_TOLERANCE_M,
        takeoff_altitude_tolerance_m=TAKEOFF_ALTITUDE_TOLERANCE_M,
        takeoff_timeout_s=TAKEOFF_TIMEOUT_S,
        landing_altitude_tolerance_m=LANDING_ALTITUDE_TOLERANCE_M,
        landing_speed_tolerance_mps=LANDING_SPEED_TOLERANCE_MPS,
    )

    # share these instances between the HTTP routes, WebSocket, and mission monitor
    app.state.telemetry_hub = telemetry_hub
    app.state.mission_hub = mission_hub
    app.state.mission_service = mission_service
    app.state.vehicle = vehicle

    try:
        # one MAVLink reader handles aircraft messages and forwards position samples
        # to TelemetryHub, separate receivers could consume each other's messages
        await vehicle.start()
        yield
    finally:
        # stop mission monitoring before closing the vehicle connection
        await mission_service.shutdown()
        await vehicle.stop()
