from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import (
    DEFAULT_LANE_SPACING_M,
    DEFAULT_SEARCH_ALTITUDE_M,
    LANDING_ALTITUDE_TOLERANCE_M,
    LANDING_SPEED_TOLERANCE_MPS,
    MAVLINK_ENDPOINT,
    CAMERA_RECEIVER_HOST,
    CAMERA_RECEIVER_PORT,
    CAMERA_STALE_AFTER_S,
    CAMERA_STARTUP_GRACE_S,
    CAMERA_STREAM_MAX_FPS,
    CAMERA_STREAM_WIDTH_PX,
    CAMERA_STREAM_JPEG_QUALITY,
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
from app.vision.camera_receiver import CameraReceiver
from app.vision.vision_runtime import VisionRuntime
from app.vision.candidate_store import CandidateStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    telemetry_hub = TelemetryHub()
    mission_hub = MissionHub()
    connection = create_mavlink_connection(MAVLINK_ENDPOINT)
    vehicle = MavlinkVehicle(connection=connection, telemetry_hub=telemetry_hub)

    camera_receiver = CameraReceiver(host=CAMERA_RECEIVER_HOST, port=CAMERA_RECEIVER_PORT)
    vision_runtime = VisionRuntime(
        camera_receiver, stale_after_s=CAMERA_STALE_AFTER_S,
        startup_grace_s=CAMERA_STARTUP_GRACE_S, max_stream_fps=CAMERA_STREAM_MAX_FPS,
        stream_width_px=CAMERA_STREAM_WIDTH_PX, stream_jpeg_quality=CAMERA_STREAM_JPEG_QUALITY,
    )

    candidate_store = CandidateStore(session_id=vision_runtime.session_id)

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
    app.state.camera_receiver = camera_receiver
    app.state.vision_runtime = vision_runtime
    app.state.candidate_store = candidate_store

    try:
        # one MAVLink reader handles aircraft messages and forwards position samples
        # to TelemetryHub, separate receivers could consume each other's messages
        await vehicle.start()
        camera_receiver.start()
        await vision_runtime.start()
        yield
    finally:
        # stop mission monitoring before closing the vehicle connection
        await mission_service.shutdown()
        await vision_runtime.stop()
        camera_receiver.stop()
        await vehicle.stop()
