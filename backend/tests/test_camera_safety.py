"""Camera-source safety and mission integration: no SITL connection required."""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from app.api.schemas.mission_execution import (
    mission_execution_response_from_domain,
    mission_update_event_from_snapshot,
)
from app.domain.models.mission_execution import MissionExecution, ResumeCheckpoint
from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode
from app.mission.mission_service import MissionService
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError
from app.vision.camera_safety import CameraSafetyWatchdog
from app.vision.vision_runtime import VisionRuntime


class Camera:
    def __init__(self):
        self.arrived_at = None
        self.count = 0

    def stats(self):
        return self.count, True, self.arrived_at

    def latest(self):
        return None

    def arrive(self, age_s=0):
        self.count += 1
        self.arrived_at = time.monotonic() - age_s


class Vehicle:
    def __init__(self):
        self.armed = False
        self.connected = True
        self.mode = "STABILIZE"
        self.rtl_count = 0
        self.takeoffs = 0
        self.guided = 0
        self.targets = []
        self.fail_rtl = False

    def get_status(self):
        return VehicleStatus(
            connected=self.connected, armed=self.armed, flight_mode=self.mode,
            last_heartbeat_utc=datetime.now(timezone.utc),
        )

    async def set_guided(self):
        self.guided += 1
        self.mode = "GUIDED"

    async def arm(self):
        self.armed = True

    async def takeoff(self, altitude_m):
        self.takeoffs += 1

    async def go_to(self, target):
        self.targets.append(target)

    async def hold(self):
        self.mode = "BRAKE"

    async def return_to_launch(self):
        self.rtl_count += 1
        if self.fail_rtl:
            raise VehicleCommandError("RTL was not acknowledged")
        self.mode = "RTL"


def sample(sequence=1, altitude=0):
    return TelemetrySample(
        sequence=sequence, timestamp_utc=datetime.now(timezone.utc),
        latitude_deg=30.2672, longitude_deg=-97.7431,
        relative_altitude_m=altitude, heading_deg=0.0, ground_speed_mps=0.0,
    )


async def setup():
    camera = Camera()
    vision = VisionRuntime(camera)
    await vision.start()
    hub = TelemetryHub()
    events = MissionHub()
    vehicle = Vehicle()
    mission = MissionService(
        telemetry_hub=hub, vehicle=vehicle, mission_hub=events,
        default_planning_settings=PlanningSettings(search_altitude_m=25, lane_spacing_m=20),
        max_telemetry_age_s=3, max_transit_distance_m=2000,
        max_route_distance_m=10000, takeoff_timeout_s=0.5,
        camera_available=vision.camera_ready,
    )
    await hub.publish(sample())
    plan = await mission.preview_route(SearchArea(vertices=(
        GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7431),
        GeoPoint(latitude_deg=30.2672, longitude_deg=-97.7429),
        GeoPoint(latitude_deg=30.2674, longitude_deg=-97.7429),
        GeoPoint(latitude_deg=30.2674, longitude_deg=-97.7431),
    )))
    return camera, vision, hub, events, vehicle, mission, plan


async def start_mission(camera, hub, mission, plan, *, altitude=25):
    camera.arrive()
    task = asyncio.create_task(mission.commence(plan.plan_id))
    await asyncio.sleep(0)
    await hub.publish(sample(2, altitude=altitude))
    return await task


@pytest.mark.asyncio
async def test_commence_preflight_blocks_missing_camera_without_arming():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    try:
        with pytest.raises(MissionExecutionError) as exc:
            await mission.commence(plan.plan_id)
        assert exc.value.code == MissionExecutionErrorCode.VEHICLE_NOT_READY
        assert exc.value.details == {"reason": "CAMERA_UNAVAILABLE"}
        assert mission.state == MissionState.PLANNING
        assert mission.current_execution is None
        assert vehicle.guided == vehicle.takeoffs == vehicle.rtl_count == 0
        assert events.subscribe()[1] is None
        camera.arrive()
        await start_mission(camera, hub, mission, plan)
        assert mission.state == MissionState.RUNNING
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_camera_loss_triggers_one_locked_rtl_and_reason_survives_landing():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        execution = await start_mission(camera, hub, mission, plan)
        initial_target = execution.active_target
        camera.arrive(age_s=4.0)
        await watchdog.check_once()
        assert vehicle.rtl_count == 0
        # Fresh source frames reset short outages.
        camera.arrive()
        await watchdog.check_once()
        assert vision.status().automatic_rtl_triggered is False
        camera.arrive(age_s=5.1)
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
        assert mission.state == MissionState.RETURNING_HOME
        assert execution.resume_checkpoint.active_target == initial_target
        assert execution.interruption_reason == "CAMERA_UNAVAILABLE"
        assert vision.status().automatic_rtl_triggered is True
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
        response = mission_execution_response_from_domain(execution)
        assert response.model_dump(by_alias=True)["interruptionReason"] == "CAMERA_UNAVAILABLE"
        snapshot = events.subscribe()[1]
        assert snapshot.interruption_reason == "CAMERA_UNAVAILABLE"
        event = mission_update_event_from_snapshot(snapshot)
        assert event.model_dump(by_alias=True, exclude_none=True)["data"]["interruptionReason"] == "CAMERA_UNAVAILABLE"
        vehicle.armed = False
        await hub.publish(sample(3, altitude=0))
        async def wait_for_landing():
            while mission.state != MissionState.INTERRUPTED:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_landing(), timeout=1)
        assert execution.interruption_reason == "CAMERA_UNAVAILABLE"
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_continue_preflight_rejects_dead_camera_and_clears_reason_after_resume():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    try:
        execution = await start_mission(camera, hub, mission, plan)
        await mission.stop(execution.execution_id)
        vehicle.armed = False
        await hub.publish(sample(3, altitude=0))
        async def wait_for_landing():
            while mission.state != MissionState.INTERRUPTED:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_landing(), timeout=1)
        assert execution.interruption_reason == "OPERATOR_STOP"
        camera.arrive(age_s=6)
        before_calls = vehicle.takeoffs
        with pytest.raises(MissionExecutionError) as exc:
            await mission.continue_mission(execution.execution_id)
        assert exc.value.details["reason"] == "CAMERA_UNAVAILABLE"
        assert mission.state == MissionState.INTERRUPTED
        assert execution.interruption_reason == "OPERATOR_STOP"
        assert vehicle.takeoffs == before_calls
        camera.arrive()
        continue_task = asyncio.create_task(mission.continue_mission(execution.execution_id))
        await asyncio.sleep(0)
        await hub.publish(sample(4, altitude=25))
        result = await continue_task
        assert result is execution and result.state == MissionState.RUNNING
        assert result.interruption_reason is None
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_paused_camera_loss_rtl_and_detector_browser_disconnect_no_action():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        execution = await start_mission(camera, hub, mission, plan)
        await mission.pause(execution.execution_id)
        # Health checks see only backend capture. Neither detector status nor
        # browser viewer count is an input to this watchdog.
        vision._set_detector_state("UNAVAILABLE")
        await watchdog.check_once()
        assert vehicle.rtl_count == 0
        camera.arrive(age_s=5.1)
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
        assert mission.state == MissionState.RETURNING_HOME
        assert execution.interruption_reason == "CAMERA_UNAVAILABLE"
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_no_active_mission_or_initial_camera_startup_never_triggers_rtl():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        vision._started_at_s = time.monotonic() - 10
        await watchdog.check_once()
        assert vehicle.rtl_count == 0
        assert vision.status().automatic_rtl_triggered is False
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_failed_rtl_is_reported_not_confirmed_and_is_not_repeated():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        execution = await start_mission(camera, hub, mission, plan)
        vehicle.fail_rtl = True
        camera.arrive(age_s=5.1)
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
        assert mission.state == MissionState.RUNNING
        assert execution.interruption_reason is None
        assert mission.background_execution_error.code == MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED
        latest = events.subscribe()[1]
        assert latest.runtime_error is not None
        assert latest.runtime_error.code == "VEHICLE_COMMAND_FAILED"
        assert latest.state == MissionState.RUNNING
        assert vision.status().automatic_rtl_triggered is True
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_operator_stop_keeps_reason_and_watchdog_cannot_issue_second_rtl():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        execution = await start_mission(camera, hub, mission, plan)
        await mission.stop(execution.execution_id)
        assert execution.interruption_reason == "OPERATOR_STOP"
        camera.arrive(age_s=6)
        await watchdog.check_once()
        assert vehicle.rtl_count == 1
        assert vision.status().automatic_rtl_triggered is False
    finally:
        await mission.shutdown()
        await vision.stop()


def test_watchdog_rejects_inconsistent_thresholds():
    camera = Camera()
    vision = VisionRuntime(camera, stale_after_s=6)
    with pytest.raises(ValueError):
        CameraSafetyWatchdog(vision, object(), outage_after_s=5)


@pytest.mark.asyncio
async def test_camera_dies_during_takeoff_rejects_route_and_attempts_recovery_rtl():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    original_takeoff = vehicle.takeoff

    async def takeoff_and_lose_camera(altitude_m):
        await original_takeoff(altitude_m)
        camera.arrive(age_s=6)

    vehicle.takeoff = takeoff_and_lose_camera
    try:
        camera.arrive()
        task = asyncio.create_task(mission.commence(plan.plan_id))
        await asyncio.sleep(0)
        await hub.publish(sample(2, altitude=25))
        with pytest.raises(MissionExecutionError) as exc:
            await task
        assert exc.value.details == {"reason": "CAMERA_UNAVAILABLE"}
        assert vehicle.rtl_count == 1
        assert mission.state == MissionState.RETURNING_HOME
        assert mission.current_execution.interruption_reason == "CAMERA_UNAVAILABLE"
        assert vehicle.targets == []
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_operator_stop_race_with_watchdog_issues_only_one_rtl():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    watchdog = CameraSafetyWatchdog(vision, mission)
    try:
        execution = await start_mission(camera, hub, mission, plan)
        entered_rtl = asyncio.Event()
        allow_rtl = asyncio.Event()
        original_rtl = vehicle.return_to_launch

        async def held_rtl():
            entered_rtl.set()
            await allow_rtl.wait()
            await original_rtl()

        vehicle.return_to_launch = held_rtl
        operator_stop = asyncio.create_task(mission.stop(execution.execution_id))
        await entered_rtl.wait()
        camera.arrive(age_s=6)
        watchdog_check = asyncio.create_task(watchdog.check_once())
        await asyncio.sleep(0)
        allow_rtl.set()
        await asyncio.gather(operator_stop, watchdog_check)
        assert vehicle.rtl_count == 1
        assert mission.state == MissionState.RETURNING_HOME
        assert execution.interruption_reason == "OPERATOR_STOP"
        assert vision.status().automatic_rtl_triggered is False
    finally:
        await mission.shutdown()
        await vision.stop()


@pytest.mark.asyncio
async def test_camera_dies_during_takeoff_and_recovery_rtl_unconfirmed():
    camera, vision, hub, events, vehicle, mission, plan = await setup()
    original_takeoff = vehicle.takeoff

    async def takeoff_and_lose_camera(altitude_m):
        await original_takeoff(altitude_m)
        camera.arrive(age_s=6)

    vehicle.takeoff = takeoff_and_lose_camera
    vehicle.fail_rtl = True
    try:
        camera.arrive()
        task = asyncio.create_task(mission.commence(plan.plan_id))
        await asyncio.sleep(0)
        await hub.publish(sample(2, altitude=25))
        with pytest.raises(MissionExecutionError):
            await task
        assert vehicle.rtl_count == 1
        assert mission.state != MissionState.RETURNING_HOME
        assert mission.background_execution_error.code == MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED
        latest = events.subscribe()[1]
        assert latest.runtime_error is not None
        assert latest.interruption_reason is None
    finally:
        await mission.shutdown()
        await vision.stop()


def test_watchdog_rejects_nonfinite_timing():
    camera = Camera()
    vision = VisionRuntime(camera)
    with pytest.raises(ValueError):
        CameraSafetyWatchdog(vision, object(), outage_after_s=float("nan"))
    with pytest.raises(ValueError):
        CameraSafetyWatchdog(vision, object(), poll_period_s=float("inf"))


@pytest.mark.asyncio
async def test_camera_safety_flag_broadcasts_status_without_duplicate_revisions():
    camera = Camera()
    vision = VisionRuntime(camera)
    await vision.start()
    queue, initial = vision.subscribe()
    try:
        assert not initial.automatic_rtl_triggered
        vision.set_automatic_rtl_triggered(True)
        announced = queue.get_nowait()
        assert announced.revision == initial.revision + 1
        assert announced.automatic_rtl_triggered
        vision.set_automatic_rtl_triggered(True)
        assert queue.empty()
        vision.set_automatic_rtl_triggered(False)
        cleared = queue.get_nowait()
        assert cleared.revision == announced.revision + 1
        assert not cleared.automatic_rtl_triggered
    finally:
        vision.unsubscribe(queue)
        await vision.stop()
