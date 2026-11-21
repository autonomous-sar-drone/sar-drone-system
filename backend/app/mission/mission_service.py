import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.domain.models.mission_execution import MissionExecution, ResumeCheckpoint, InterruptionReason
from app.domain.models.mission_plan import MissionPlan
from app.domain.models.mission_state import FlightActivity, HoldReason, MissionState
from app.domain.models.route import AltitudeReference, PlanningSettings, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.geospatial.distance import geodesic_distance_m
from app.geospatial.terrain_grid import TerrainGrid
from app.geospatial.terrain_relocation import (
    RouteDepartureAnchor, plan_terrain_relocation_chain,
)
from app.geospatial.terrain_navigation import (
    NavigationCommand, LookaheadTarget, build_safe_transit,
    build_terrain_navigation, terrain_lookahead_target,
)
from app.geospatial.route_planning import generate_route
from app.geospatial.search_area_validation import validate_search_area
from app.mission.manual_input_safety import (
    ManualInputFrame, ManualInputSafety, ManualSafetyLimits,
    ManualSafetyDecision, NEUTRAL_VELOCITY,
)
from app.mission.errors import (
    MissionExecutionError,
    MissionExecutionErrorCode,
    MissionPlanningError,
    MissionPlanningErrorCode,
)
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError


logger = logging.getLogger(__name__)


class VehicleControl(Protocol):
    """Application-level vehicle operations MissionService is allowed to use."""

    def get_status(self) -> VehicleStatus: ...

    async def set_guided(self) -> None: ...

    async def arm(self) -> None: ...

    async def takeoff(self, altitude_m: float) -> None: ...

    async def go_to(self, target: Waypoint) -> None: ...

    async def hold(self) -> None: ...

    async def return_to_launch(self) -> None: ...

    async def configure_safe_rtl(self, altitude_m: float) -> None: ...

    async def send_manual_velocity(
        self, forward_mps: float, right_mps: float,
        up_mps: float, yaw_rate_deg_s: float,
    ) -> None: ...


class MissionService:
    def __init__(
        self,
        telemetry_hub: TelemetryHub,
        default_planning_settings: PlanningSettings,
        max_telemetry_age_s: float,
        max_transit_distance_m: float,
        max_route_distance_m: float,
        vehicle: VehicleControl | None = None,
        target_reached_tolerance_m: float = 3.0,
        target_altitude_tolerance_m: float = 2.0,
        takeoff_altitude_tolerance_m: float = 1.5,
        takeoff_timeout_s: float = 30.0,
        landing_altitude_tolerance_m: float = 0.75,
        landing_speed_tolerance_mps: float = 0.75,
        mission_hub: MissionHub | None = None,
        camera_available: Callable[[], bool] | None = None,
        terrain_grid: TerrainGrid | None = None,
        terrain_step_m: float = 5.0,
        terrain_lookahead_m: float = 8.0,
        rtl_home_altitude_m: float | None = None,
    ) -> None:
        self._telemetry_hub = telemetry_hub
        self._mission_hub = mission_hub
        # The production bootstrap provides camera health. Optional for older service tests.
        self._camera_available = camera_available
        self._past_executions: list[MissionExecution] = []
        self._active_target_sent_at_utc: datetime | None = None
        self._default_planning_settings = default_planning_settings
        self._max_telemetry_age_s = max_telemetry_age_s
        self._max_transit_distance_m = max_transit_distance_m
        self._max_route_distance_m = max_route_distance_m
        self._vehicle = vehicle
        self._terrain_grid = terrain_grid
        self._terrain_step_m = terrain_step_m
        self._rtl_home_altitude_m = rtl_home_altitude_m
        if not 3 <= terrain_lookahead_m <= 30:
            raise ValueError("Terrain lookahead must be between 3 and 30 m")
        self._terrain_lookahead_m = terrain_lookahead_m
        self._terrain_launch_origin: Waypoint | None = None
        self._recovery_origin: Waypoint | None = None
        self._recovery_segment_ends: tuple[int, ...] = ()
        self._last_guidance_target: Waypoint | None = None
        self._last_guidance_sent_at: datetime | None = None
        self._navigation_commands: tuple[NavigationCommand, ...] = ()
        self._navigation_index = 0
        self._navigation_target: Waypoint | None = None
        self._recovery_commands: tuple[Waypoint, ...] = ()
        self._recovery_index = 0
        self._preserve_resume_anchor = False
        self._route_departure_anchor: RouteDepartureAnchor | None = None
        self._recovery_climb_target: Waypoint | None = None

        self._target_reached_tolerance_m = target_reached_tolerance_m
        self._target_altitude_tolerance_m = target_altitude_tolerance_m
        self._takeoff_altitude_tolerance_m = takeoff_altitude_tolerance_m
        self._takeoff_timeout_s = takeoff_timeout_s
        self._landing_altitude_tolerance_m = landing_altitude_tolerance_m
        self._landing_speed_tolerance_mps = landing_speed_tolerance_mps

        self._state = MissionState.IDLE
        self._proposed_plan: MissionPlan | None = None
        self._current_execution: MissionExecution | None = None

        # route previews have their own lock, mission actions share one lock
        # so pause, stop, and resume cannot overlap
        self._planning_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

        self._execution_monitor_task: asyncio.Task[None] | None = None
        self._return_terminal_state: MissionState | None = None
        self._resuming_to_checkpoint = False
        self._background_execution_error: MissionExecutionError | None = None
        self._last_stall_log_at_utc: datetime | None = None

        # One authoritative manual socket/epoch at a time. The action lock
        # serializes every movement with Stop, camera-loss RTL and search guidance.
        self._manual_epoch: str | None = None
        self._manual_safety: ManualInputSafety | None = None
        self._manual_started_at_s: float | None = None
        self._manual_limits = ManualSafetyLimits()
        self._manual_last_feedback: str | None = None

    @property
    def state(self) -> MissionState:
        return self._state

    @property
    def proposed_plan(self) -> MissionPlan | None:
        return self._proposed_plan

    @property
    def current_execution(self) -> MissionExecution | None:
        return self._current_execution

    @property
    def past_executions(self) -> tuple[MissionExecution, ...]:
        return tuple(self._past_executions)

    @property
    def background_execution_error(self) -> MissionExecutionError | None:
        return self._background_execution_error

    async def preview_route(
        self,
        search_area: SearchArea,
        planning_settings: PlanningSettings | None = None,
    ) -> MissionPlan:
        # only one preview at a time can replace the proposed plan
        async with self._planning_lock:
            async with self._action_lock:
                if self._state not in {
                    MissionState.IDLE, MissionState.PLANNING,
                    MissionState.INTERRUPTED, MissionState.COMPLETED,
                }:
                    raise MissionPlanningError(
                        code=MissionPlanningErrorCode.MISSION_ACTION_NOT_ALLOWED,
                        message="A route preview cannot be generated in the current mission state.",
                        details={"current_state": self._state.value},
                    )

                # keep the old execution in history when replanning
                # stop its telemetry monitor before replacing the plan
                if self._state in {MissionState.INTERRUPTED, MissionState.COMPLETED}:
                    await self.shutdown()
                    if self._current_execution is not None:
                        self._past_executions.append(self._current_execution)
                    self._current_execution = None
                    self._active_target_sent_at_utc = None
                    self._reset_terrain_navigation()
                    if self._mission_hub is not None:
                        self._mission_hub.clear()

                # a new preview invalidates the previous proposed plan
                self._state = MissionState.PLANNING
                self._proposed_plan = None

            validation = validate_search_area(search_area)
            if not validation.valid:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.INVALID_SEARCH_AREA,
                    message="The search area geometry is invalid.",
                    details={
                        "reason": validation.reason.value
                        if validation.reason
                        else None
                    },
                )

            telemetry = await self._require_fresh_telemetry_for_planning()

            start_reference = GeoPoint(
                latitude_deg=telemetry.latitude_deg,
                longitude_deg=telemetry.longitude_deg,
            )
            settings = planning_settings or self._default_planning_settings

            route, metrics = generate_route(
                search_area=search_area,
                planning_settings=settings,
                start_reference=start_reference,
            )

            if metrics.distance_to_start_m > self._max_transit_distance_m:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.SEARCH_AREA_OUT_OF_RANGE,
                    message="The search area is too far from the current vehicle position.",
                    details={
                        "distance_to_start_m": metrics.distance_to_start_m,
                        "max_transit_distance_m": self._max_transit_distance_m,
                    },
                )

            if metrics.route_distance_m > self._max_route_distance_m:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.ROUTE_TOO_LONG,
                    message="The generated route is too long for the configured mission limits.",
                    details={
                        "route_distance_m": metrics.route_distance_m,
                        "max_route_distance_m": self._max_route_distance_m,
                    },
                )

            plan = MissionPlan(
                plan_id=str(uuid4()),
                search_area=search_area,
                planning_settings=settings,
                route=route,
                route_metrics=metrics,
            )

            self._proposed_plan = plan

            return plan

    async def commence(self, plan_id: str) -> MissionExecution:
        """Start the current proposed plan and return once search execution is running."""
        async with self._action_lock:
            self._raise_background_error_if_present()

            if self._state != MissionState.PLANNING:
                self._raise_action_not_allowed("commence")

            plan = self._require_current_plan(plan_id)
            vehicle = self._require_ready_vehicle()

            # subscribe before reading the latest position, so no samples are
            # missed between the starting snapshot and ongoing history
            telemetry_queue, latest = await self._telemetry_hub.subscribe()

            try:
                telemetry = self._validate_execution_telemetry(latest)
                terrain_commands = self._build_internal_commands(plan, telemetry)
            except MissionExecutionError:
                await self._telemetry_hub.unsubscribe(telemetry_queue)
                raise

            self._terrain_launch_origin = Waypoint(
                telemetry.latitude_deg, telemetry.longitude_deg,
                terrain_commands[0].target.altitude_m if terrain_commands else 0.0,
                AltitudeReference.HOME_RELATIVE,
            ) if terrain_commands else None
            self._last_guidance_target = None
            self._last_guidance_sent_at = None
            self._navigation_commands = terrain_commands
            self._navigation_index = 0
            self._navigation_target = terrain_commands[0].target if terrain_commands else None
            self._recovery_commands = ()
            self._recovery_index = 0
            self._preserve_resume_anchor = False
            self._route_departure_anchor = None
            self._recovery_climb_target = None

            execution = MissionExecution(
                execution_id=str(uuid4()),
                plan_id=plan.plan_id,
                state=MissionState.PREFLIGHT_CHECK,
                telemetry_history=[telemetry],
            )

            self._current_execution = execution
            self._set_state(MissionState.PREFLIGHT_CHECK)
            self._return_terminal_state = None
            self._resuming_to_checkpoint = False
            self._background_execution_error = None

            # Camera availability is a preflight guard, before any flight command.
            # A rejection must not leave an execution or telemetry subscription behind.
            try:
                self._require_camera_ready()
            except MissionExecutionError:
                self._set_state(MissionState.PLANNING)
                self._current_execution = None
                if self._mission_hub is not None:
                    self._mission_hub.clear()
                await self._telemetry_hub.unsubscribe(telemetry_queue)
                raise

            self._start_execution_monitor(
                execution_id=execution.execution_id,
                telemetry_queue=telemetry_queue,
            )

            # only mark RUNNING after GUIDED, arming, takeoff, and first target work
            # before arming, failed setup can return to planning
            # after arming, a failed setup needs an RTL attempt
            try:
                takeoff_altitude = self._terrain_takeoff_altitude(
                    plan.planning_settings.search_altitude_m, telemetry,
                    self._navigation_target,
                )
                await self._prepare_vehicle_for_search(
                    vehicle=vehicle, search_altitude_m=takeoff_altitude,
                )

                # A long takeoff may outlast the last healthy frame. Check again
                # before beginning the coverage route; recovery handles airborne RTL.
                self._require_camera_ready()
                first_target = plan.route.waypoints[0]
                if terrain_commands:
                    await self._send_terrain_guidance(
                        vehicle,
                        terrain_lookahead_target(
                            self._terrain_grid, telemetry.latitude_deg, telemetry.longitude_deg,
                            self._terrain_launch_origin,
                            tuple(command.target for command in terrain_commands
                                  if command.visible_index == 0),
                            self._terrain_lookahead_m,
                        ),
                        force=True,
                    )
                else:
                    await self._send_navigation_target(vehicle, first_target)
            except MissionExecutionError as error:
                await self._recover_failed_execution_setup(
                    execution=execution,
                    vehicle=vehicle,
                    grounded_state=MissionState.PLANNING,
                    discard_execution_when_grounded=True,
                    interruption_reason=self._camera_reason_from_error(error),
                )
                raise

            execution.active_target = first_target
            execution.flight_activity = FlightActivity.SEARCHING
            execution.hold_reason = None
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._set_state(MissionState.RUNNING)

            return execution

    async def capture_route_departure_anchor(self, execution_id: str) -> RouteDepartureAnchor:
        """Capture the original search departure before a future manual/revisit takeover.

        This records navigation intent only; it does not transfer vehicle control.
        The eventual manual authority handler must call this under its own
        confirmed takeover flow, never in response to a browser position.
        """
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)
            if self._terrain_grid is None or execution.state != MissionState.RUNNING:
                self._raise_action_not_allowed("capture_route_departure_anchor")
            if self._route_departure_anchor is not None:
                return self._route_departure_anchor
            # A newly recorded departure must be on the search path, not on
            # a Stop/Continue or another relocation leg.
            if self._recovery_commands:
                self._raise_action_not_allowed("capture_route_departure_anchor")
            if execution.active_target is None:
                self._raise_action_not_allowed("capture_route_departure_anchor")
            _, latest = await self._telemetry_hub.get_latest()
            sample = self._validate_execution_telemetry(latest)
            try:
                self._terrain_grid.height_geo(sample.latitude_deg, sample.longitude_deg)
            except ValueError as exc:
                raise self._terrain_error(exc) from exc
            self._route_departure_anchor = RouteDepartureAnchor(
                position=Waypoint(sample.latitude_deg, sample.longitude_deg,
                                  sample.relative_altitude_m, AltitudeReference.HOME_RELATIVE),
                unfinished_target=execution.active_target,
                completed_count=len(execution.completed_targets),
            )
            return self._route_departure_anchor

    def _require_manual_telemetry(self, sample: TelemetrySample) -> None:
        # The manual safety gate requires a tighter freshness window than route
        # planning; do not advertise readiness on a 2-3-second-old position.
        age = self._telemetry_age_s(sample)
        if not 0 <= age <= self._manual_limits.telemetry_max_age_s:
            raise MissionExecutionError(
                MissionExecutionErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                "Fresh vehicle telemetry is required for manual control.",
                {"reason": "STALE_TELEMETRY"},
            )

    async def take_controls(self, execution_id: str) -> MissionExecution:
        """Confirm BRAKE before assigning any manual authority.

        Browser movement is not enabled by this HTTP response alone: a single
        WebSocket must subsequently establish GUIDED and receive a new epoch.
        """
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)
            if execution.state not in (MissionState.RUNNING, MissionState.PAUSED):
                self._raise_action_not_allowed("take_controls")
            if execution.flight_activity not in (FlightActivity.SEARCHING, FlightActivity.HOLDING):
                self._raise_action_not_allowed("take_controls")
            self._require_camera_ready()
            vehicle = self._require_ready_vehicle()
            status = vehicle.get_status()
            if not status.armed or status.flight_mode not in ("GUIDED", "BRAKE"):
                self._raise_action_not_allowed("take_controls")
            if self._terrain_grid is None:
                raise MissionExecutionError(
                    MissionExecutionErrorCode.VEHICLE_NOT_READY,
                    "Terrain data is required for manual flight.",
                    {"reason": "TERRAIN_UNAVAILABLE"},
                )
            _, sample = await self._telemetry_hub.get_latest()
            sample = self._validate_execution_telemetry(sample)
            self._require_manual_telemetry(sample)
            try:
                self._terrain_grid.height_geo(sample.latitude_deg, sample.longitude_deg)
            except ValueError as exc:
                raise self._terrain_error(exc) from exc
            # A running search records the original departure once; later manual
            # diversions must not replace it. Resume uses terrain relocation via A.
            if (self._route_departure_anchor is None and
                    not self._recovery_commands and execution.active_target is not None):
                departure = RouteDepartureAnchor(
                    position=Waypoint(sample.latitude_deg, sample.longitude_deg,
                                      sample.relative_altitude_m, AltitudeReference.HOME_RELATIVE),
                    unfinished_target=execution.active_target,
                    completed_count=len(execution.completed_targets),
                )
            else:
                departure = None

            # BRAKE is confirmed before disabling search progress and before
            # declaring the handoff. Nothing sends manual velocity yet.
            await self._call_vehicle(vehicle.hold())
            if departure is not None:
                self._route_departure_anchor = departure
            self._last_guidance_target = None
            self._last_guidance_sent_at = None
            self._invalidate_manual_channel_locked()
            execution.flight_activity = FlightActivity.MANUAL_CONTROL
            execution.hold_reason = None
            execution.manual_control_ready = False
            self._set_state(MissionState.PAUSED)
            return execution

    async def give_controls(self, execution_id: str) -> MissionExecution:
        """Drop input authority, neutralize, then confirm BRAKE; never auto-resume."""
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)
            if execution.state != MissionState.PAUSED or execution.flight_activity != FlightActivity.MANUAL_CONTROL:
                self._raise_action_not_allowed("give_controls")
            self._invalidate_manual_channel_locked()
            execution.manual_control_ready = False
            self._publish_execution()
            vehicle = self._require_ready_vehicle()
            try:
                await self._neutral_if_guided_locked(vehicle)
            except MissionExecutionError:
                logger.warning("Manual neutral command failed; attempting confirmed BRAKE")
            if not await self._brake_if_manual_mode_locked(vehicle):
                raise MissionExecutionError(
                    MissionExecutionErrorCode.VEHICLE_NOT_READY,
                    "Vehicle mode changed before manual control could be released.",
                    {"reason": "MODE_CHANGED"},
                )
            execution.flight_activity = FlightActivity.HOLDING
            execution.hold_reason = HoldReason.MANUAL_RELEASE
            self._publish_execution()
            return execution

    def _invalidate_manual_channel_locked(self) -> None:
        self._manual_epoch = None
        self._manual_safety = None
        self._manual_started_at_s = None
        self._manual_last_feedback = None
        if self._current_execution is not None:
            self._current_execution.manual_control_ready = False

    async def _neutral_if_guided_locked(self, vehicle: VehicleControl) -> None:
        status = vehicle.get_status()
        if status.connected and status.armed and status.flight_mode == "GUIDED":
            await self._call_vehicle(vehicle.send_manual_velocity(0.0, 0.0, 0.0, 0.0))

    async def _brake_if_manual_mode_locked(self, vehicle: VehicleControl) -> bool:
        """Never cancel a native RTL, landing, or vehicle failsafe with BRAKE."""
        status = vehicle.get_status()
        if not status.connected or not status.armed or status.flight_mode not in ("GUIDED", "BRAKE"):
            return False
        await self._call_vehicle(vehicle.hold())
        return True

    async def open_manual_channel(self) -> dict[str, object]:
        """Reserve the sole socket and issue a fresh, nonreplayable control epoch."""
        async with self._action_lock:
            execution = self._current_execution
            if (execution is None or execution.state != MissionState.PAUSED or
                    execution.flight_activity != FlightActivity.MANUAL_CONTROL or
                    self._manual_epoch is not None):
                self._raise_action_not_allowed("open_manual_channel")
            if self._terrain_grid is None:
                self._raise_action_not_allowed("open_manual_channel")
            vehicle = self._require_ready_vehicle()
            status = vehicle.get_status()
            if not status.armed or status.flight_mode not in ("BRAKE", "GUIDED"):
                self._raise_action_not_allowed("open_manual_channel")
            _, sample = await self._telemetry_hub.get_latest()
            sample = self._validate_execution_telemetry(sample)
            self._require_manual_telemetry(sample)
            try:
                self._terrain_grid.height_geo(sample.latitude_deg, sample.longitude_deg)
            except ValueError as exc:
                raise self._terrain_error(exc) from exc
            gate = ManualInputSafety(self._terrain_grid, execution.execution_id,
                                     str(uuid4()), self._manual_limits)
            # GUIDED is only enabled with a real socket requesting a new epoch.
            # A failure leaves BRAKE/PAUSED with no accepted movement authority.
            try:
                await self._call_vehicle(vehicle.set_guided())
                await self._neutral_if_guided_locked(vehicle)
            except MissionExecutionError:
                try:
                    await self._brake_if_manual_mode_locked(vehicle)
                except MissionExecutionError:
                    logger.exception("Manual socket setup failed and BRAKE could not be confirmed")
                raise
            self._manual_epoch = gate.control_epoch
            self._manual_safety = gate
            self._manual_started_at_s = time.monotonic()
            self._manual_last_feedback = None
            execution.manual_control_ready = True
            self._publish_execution()
            return {
                "executionId": execution.execution_id,
                "controlEpoch": gate.control_epoch,
                "inputMaxAgeMs": gate.limits.input_max_age_ms,
                "watchdogMs": round(gate.limits.watchdog_s * 1000),
                "sendIntervalMs": 100,
            }

    async def manual_channel_input(self, epoch: str, data: Mapping[str, Any]) -> dict[str, object]:
        """Validate input and send exactly one bounded velocity under action lock."""
        async with self._action_lock:
            if not self._manual_channel_active_locked(epoch):
                return {"sequence": 0, "accepted": False, "revoked": True}
            assert self._manual_safety is not None
            vehicle = self._require_ready_vehicle()
            if not vehicle.get_status().armed or vehicle.get_status().flight_mode != "GUIDED":
                # RTL or disarm may come from the autopilot independently. Never
                # issue neutral/BRAKE that could override native failsafe.
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                return {"sequence": 0, "accepted": False, "revoked": True}
            _, latest = await self._telemetry_hub.get_latest()
            try:
                frame = ManualInputFrame.from_wire(data)
            except (ValueError, TypeError, AttributeError):
                # Never let a malformed frame preserve the prior forward command.
                self._manual_safety.invalidate_input()
                await self._neutral_if_guided_locked(vehicle)
                return {"sequence": 0, "accepted": False}
            now_s = time.monotonic()
            # A packet arriving between watchdog ticks must not restart a
            # previously silent nonzero command without a fresh key release.
            if self._manual_safety.timed_out(now_s):
                self._manual_safety.invalidate_input()
            decision = self._manual_safety.evaluate(
                frame, latest, vehicle.get_status(), now_monotonic_s=now_s)
            velocity = decision.velocity if decision.accepted else NEUTRAL_VELOCITY
            # Errors from MAVLink invalidate the stream, not just this individual
            # packet: the controller must never keep accepting more key presses.
            try:
                await self._call_vehicle(vehicle.send_manual_velocity(
                    velocity.forward_mps, velocity.right_mps,
                    velocity.up_mps, velocity.yaw_rate_deg_s,
                ))
            except MissionExecutionError:
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                try:
                    await self._brake_if_manual_mode_locked(vehicle)
                except MissionExecutionError:
                    logger.exception("Manual movement failed and BRAKE could not be confirmed")
                raise
            feedback = self._manual_feedback_code(decision)
            result: dict[str, object] = {"sequence": frame.sequence, "accepted": decision.accepted}
            if feedback != self._manual_last_feedback:
                result["feedback"] = feedback
                result["clearFeedback"] = self._manual_last_feedback
                self._manual_last_feedback = feedback
            return result

    @staticmethod
    def _manual_feedback_code(decision: ManualSafetyDecision) -> str | None:
        return {
            "TERRAIN_FLOOR": "TERRAIN_MIN", "TERRAIN_CEILING": "TERRAIN_MAX",
            "TERRAIN_UNAVAILABLE": "TERRAIN_UNAVAILABLE",
            "VEHICLE_UNAVAILABLE": "POSITION_UNAVAILABLE",
            "HEADING_UNAVAILABLE": "POSITION_UNAVAILABLE",
        }.get(decision.code)

    def _manual_channel_active_locked(self, epoch: str) -> bool:
        execution = self._current_execution
        return (self._manual_epoch == epoch and self._manual_safety is not None and
                execution is not None and execution.state == MissionState.PAUSED and
                execution.flight_activity == FlightActivity.MANUAL_CONTROL and
                execution.manual_control_ready)

    async def manual_watchdog_tick(self, epoch: str) -> bool:
        """Return False if the socket must close and establish a new epoch."""
        async with self._action_lock:
            if not self._manual_channel_active_locked(epoch):
                return False
            assert self._manual_safety is not None
            try:
                vehicle = self._require_ready_vehicle()
            except MissionExecutionError:
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                return False
            status = vehicle.get_status()
            # Another controller changing the mode is an immediate loss of
            # manual authority; do not send a GUIDED command into RTL/BRAKE.
            if not status.armed or status.flight_mode != "GUIDED":
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                return False
            _, telemetry = await self._telemetry_hub.get_latest()
            now_s = time.monotonic()
            neutral_needed = self._manual_safety.neutral_required(
                now_monotonic_s=now_s, now_utc=datetime.now(timezone.utc),
                telemetry=telemetry, status=status,
            )
            if not neutral_needed:
                return True
            try:
                await self._neutral_if_guided_locked(vehicle)
            except MissionExecutionError:
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                try:
                    await self._brake_if_manual_mode_locked(vehicle)
                except MissionExecutionError:
                    logger.exception("Manual watchdog failed and BRAKE could not be confirmed")
                return False
            # Short gaps only neutralize velocity while remaining in GUIDED.
            # Do not repeatedly BRAKE/reopen the socket for browser timer jitter.
            # Truly silent channels are still revoked after a separate timeout.
            if (self._manual_started_at_s is not None and
                    self._manual_safety.session_silent(now_s, self._manual_started_at_s)):
                logger.warning("Manual channel silent for %.1f seconds; releasing to BRAKE",
                               self._manual_limits.session_silence_timeout_s)
                self._invalidate_manual_channel_locked()
                self._publish_execution()
                try:
                    await self._brake_if_manual_mode_locked(vehicle)
                except MissionExecutionError:
                    logger.exception("Manual channel silent: BRAKE not confirmed")
                return False
            return True

    async def close_manual_channel(self, epoch: str) -> None:
        """Socket loss neutralizes and BRAKEs, without an automatic RTL."""
        async with self._action_lock:
            if not self._manual_channel_active_locked(epoch):
                return
            self._invalidate_manual_channel_locked()
            self._publish_execution()
            vehicle = self._vehicle
            if vehicle is None or not vehicle.get_status().connected:
                return
            try:
                await self._neutral_if_guided_locked(vehicle)
            except MissionExecutionError:
                logger.warning("Manual socket neutral failed; requesting BRAKE")
            try:
                await self._brake_if_manual_mode_locked(vehicle)
            except MissionExecutionError:
                logger.exception("Manual socket closed but BRAKE could not be confirmed")

    async def pause(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.RUNNING:
                self._raise_action_not_allowed("pause")

            vehicle = self._require_ready_vehicle()

            # BRAKE has to be confirmed before the mission becomes PAUSED
            await self._call_vehicle(vehicle.hold())
            self._last_guidance_target = None
            self._last_guidance_sent_at = None
            execution.flight_activity = FlightActivity.HOLDING
            execution.hold_reason = HoldReason.OPERATOR_PAUSE
            self._set_state(MissionState.PAUSED)

            return execution

    async def resume(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            self._raise_background_error_if_present()
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.PAUSED or execution.flight_activity != FlightActivity.HOLDING:
                self._raise_action_not_allowed("resume")

            if execution.active_target is None:
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="The paused mission has no active target to resume.",
                    details={"current_state": execution.state.value},
                )

            vehicle = self._require_ready_vehicle()

            if self._terrain_grid is not None:
                _, latest = await self._telemetry_hub.get_latest()
                sample = self._validate_execution_telemetry(latest)
                if self._route_departure_anchor is not None:
                    destinations = (self._route_departure_anchor.position,)
                elif self._recovery_commands:
                    destinations = tuple(self._recovery_commands[i]
                                         for i in self._recovery_segment_ends
                                         if i >= self._recovery_index)
                else:
                    destinations = (self._navigation_target,)
                if not destinations or any(dest is None for dest in destinations):
                    self._raise_action_not_allowed("resume")
                try:
                    origin = Waypoint(sample.latitude_deg, sample.longitude_deg,
                                      sample.relative_altitude_m, AltitudeReference.HOME_RELATIVE)
                    relocation = plan_terrain_relocation_chain(
                        self._terrain_grid, origin, destinations,
                        self._require_current_plan(execution.plan_id).planning_settings.search_altitude_m,
                        spacing_m=self._terrain_step_m,
                    )
                except ValueError as exc:
                    raise self._terrain_error(exc) from exc
                self._recovery_commands = relocation.commands
                self._recovery_segment_ends = relocation.segment_ends
                self._recovery_origin = origin
                self._recovery_index = 0
                self._recovery_climb_target = relocation.climb_target
                self._resuming_to_checkpoint = True
                self._last_guidance_target = None
                self._last_guidance_sent_at = None

            await self._call_vehicle(vehicle.set_guided())
            if self._terrain_grid is not None:
                await self._guide_recovery(vehicle, sample, force=True)
            else:
                await self._send_navigation_target(vehicle, execution.active_target)

            # resend the saved target after switching back from BRAKE to GUIDED
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            execution.flight_activity = FlightActivity.SEARCHING
            execution.hold_reason = None
            self._set_state(MissionState.RUNNING)

            return execution

    async def stop(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            # stop still needs to allow RTL after a guidance error
            execution = self._require_current_execution(execution_id)
            if execution.state not in {MissionState.RUNNING, MissionState.PAUSED}:
                self._raise_action_not_allowed("stop")
            await self._interrupt_execution_locked(execution, "OPERATOR_STOP")
            return execution

    async def interrupt_due_to_camera_loss(self) -> bool:
        """Attempt one controlled interruption if a search is still active.

        The watchdog does not own MAVLink control. The same action lock as Stop
        prevents duplicate RTL when an operator action is already in progress.
        """
        async with self._action_lock:
            execution = self._current_execution
            if execution is None or execution.state not in {
                MissionState.RUNNING, MissionState.PAUSED,
            }:
                return False
            try:
                await self._interrupt_execution_locked(execution, "CAMERA_UNAVAILABLE")
            except MissionExecutionError as error:
                # No state transition to RETURNING_HOME without RTL confirmation.
                # Publish a truthful error so a disconnected aircraft is not
                # reported as safely returning.
                self._report_background_error(error)
                raise
            return True

    async def _interrupt_execution_locked(
        self, execution: MissionExecution, reason: InterruptionReason,
    ) -> None:
        manual_active = execution.flight_activity == FlightActivity.MANUAL_CONTROL
        if manual_active:
            # Revoke immediately even if heartbeat is already gone; a late
            # browser packet must not remain eligible to command the vehicle.
            self._invalidate_manual_channel_locked()
            self._publish_execution()
        vehicle = self._require_ready_vehicle()
        if manual_active:
            try:
                await self._neutral_if_guided_locked(vehicle)
            except MissionExecutionError:
                logger.warning("Could not send neutral before RTL; requesting RTL anyway")
        _, latest = await self._telemetry_hub.get_latest()
        if manual_active:
            try:
                telemetry = self._validate_execution_telemetry(latest)
            except MissionExecutionError:
                # The aircraft's own RTL controller can still be commanded even
                # when position telemetry to the dashboard is stale. A checkpoint
                # cannot truthfully be recorded without fresh coordinates.
                telemetry = None
        else:
            telemetry = self._validate_execution_telemetry(latest)

        # save where the aircraft actually stopped and which search target
        # it was following, continue needs both pieces
        resume_target = self._navigation_target or execution.active_target

        # during recovery the checkpoint is temporarily the active target
        # stopping again should still remember the unfinished search waypoint
        if (
            (self._preserve_resume_anchor if self._terrain_grid is not None
             else self._resuming_to_checkpoint)
            and execution.resume_checkpoint is not None
            and execution.resume_checkpoint.active_target is not None
        ):
            resume_target = execution.resume_checkpoint.active_target
            checkpoint = (execution.resume_checkpoint if self._terrain_grid is not None
                          else ResumeCheckpoint(telemetry=telemetry, active_target=resume_target)
                          if telemetry is not None else execution.resume_checkpoint)
        else:
            checkpoint = (ResumeCheckpoint(telemetry=telemetry, active_target=resume_target)
                          if telemetry is not None else execution.resume_checkpoint)

        execution.resume_checkpoint = checkpoint
        self._preserve_resume_anchor = False

        # wait for RTL confirmation before reporting RETURNING_HOME
        await self._call_vehicle(vehicle.return_to_launch())

        execution.active_target = None
        execution.flight_activity = None
        execution.hold_reason = None
        execution.manual_control_ready = False
        execution.interruption_reason = reason
        self._recovery_commands = ()
        self._recovery_index = 0
        self._recovery_segment_ends = ()
        self._recovery_origin = None
        self._recovery_climb_target = None
        self._last_guidance_target = None
        self._last_guidance_sent_at = None
        self._resuming_to_checkpoint = False
        self._return_terminal_state = MissionState.INTERRUPTED
        self._background_execution_error = None
        self._set_state(MissionState.RETURNING_HOME)

    async def continue_mission(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            self._raise_background_error_if_present()
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.INTERRUPTED:
                self._raise_action_not_allowed("continue")

            checkpoint = execution.resume_checkpoint
            if checkpoint is None or checkpoint.active_target is None:
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="The interrupted mission has no usable resume checkpoint.",
                    details={"current_state": execution.state.value},
                )

            plan = self._require_current_plan(execution.plan_id)
            vehicle = self._require_ready_vehicle()

            _, latest = await self._telemetry_hub.get_latest()
            telemetry = self._validate_execution_telemetry(latest)

            # keep the monitor subscribed while interrupted to preserve history
            # continue reuses it rather than creating another telemetry reader
            if (
                self._execution_monitor_task is None
                or self._execution_monitor_task.done()
            ):
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="The interrupted mission is no longer being monitored.",
                    details={"current_state": execution.state.value},
                )

            if self._terrain_grid is not None:
                if not self._navigation_commands or self._navigation_target is None:
                    raise self._terrain_error(ValueError("Unfinished terrain route is missing"))
                try:
                    departure_altitude = self._terrain_takeoff_altitude(
                        plan.planning_settings.search_altitude_m,
                        telemetry, self._navigation_target,
                    )
                    departure = Waypoint(telemetry.latitude_deg, telemetry.longitude_deg,
                                         departure_altitude, AltitudeReference.HOME_RELATIVE)
                    if self._route_departure_anchor is not None:
                        destinations = (self._route_departure_anchor.position,)
                    else:
                        checkpoint_ground = self._terrain_grid.height_geo(
                            checkpoint.telemetry.latitude_deg,
                            checkpoint.telemetry.longitude_deg,
                        )
                        checkpoint_target = Waypoint(
                            checkpoint.telemetry.latitude_deg, checkpoint.telemetry.longitude_deg,
                            max(checkpoint.telemetry.relative_altitude_m,
                                checkpoint_ground + plan.planning_settings.search_altitude_m + 1.0),
                            AltitudeReference.HOME_RELATIVE,
                        )
                        destinations = (checkpoint_target, self._navigation_target)
                    relocation = plan_terrain_relocation_chain(
                        self._terrain_grid, departure, destinations,
                        plan.planning_settings.search_altitude_m, self._terrain_step_m,
                    )
                    recovery_commands = relocation.commands
                except ValueError as exc:
                    raise self._terrain_error(exc) from exc
            else:
                recovery_commands = ()
                relocation = None

            self._set_state(MissionState.PREFLIGHT_CHECK)
            self._return_terminal_state = None
            self._resuming_to_checkpoint = False
            self._background_execution_error = None

            # Camera guard runs while the mission is in PREFLIGHT_CHECK, before
            # sending GUIDED, arm or takeoff. Preserve the previous checkpoint
            # and interruption cause if the check rejects Continue.
            try:
                self._require_camera_ready()
            except MissionExecutionError:
                self._return_terminal_state = MissionState.INTERRUPTED
                self._set_state(MissionState.INTERRUPTED)
                raise

            try:
                takeoff_altitude = self._terrain_takeoff_altitude(
                    plan.planning_settings.search_altitude_m, telemetry,
                    recovery_commands[0] if recovery_commands else None,
                )
                await self._prepare_vehicle_for_search(
                    vehicle=vehicle, search_altitude_m=takeoff_altitude,
                )
                self._require_camera_ready()

                checkpoint_target = Waypoint(
                    latitude_deg=checkpoint.telemetry.latitude_deg,
                    longitude_deg=checkpoint.telemetry.longitude_deg,
                    altitude_m=checkpoint.telemetry.relative_altitude_m,
                    altitude_reference=AltitudeReference.HOME_RELATIVE,
                )
                if recovery_commands:
                    relocation_start = departure
                    first_segment = relocation.segment_ends[0]
                    await self._send_terrain_guidance(
                        vehicle,
                        terrain_lookahead_target(
                            self._terrain_grid, telemetry.latitude_deg, telemetry.longitude_deg,
                            relocation_start, recovery_commands[:first_segment + 1],
                            self._terrain_lookahead_m,
                        ),
                        force=True,
                    )
                else:
                    await self._send_navigation_target(vehicle, checkpoint_target)
            except MissionExecutionError as error:
                await self._recover_failed_execution_setup(
                    execution=execution,
                    vehicle=vehicle,
                    grounded_state=MissionState.INTERRUPTED,
                    discard_execution_when_grounded=False,
                    interruption_reason=self._camera_reason_from_error(error),
                )
                raise

            execution.active_target = (
                self._route_departure_anchor.unfinished_target
                if self._route_departure_anchor is not None else checkpoint_target
            )
            self._recovery_commands = recovery_commands
            self._recovery_index = 0
            self._recovery_segment_ends = (relocation.segment_ends if relocation else ())
            self._recovery_origin = (departure if recovery_commands else None)
            self._recovery_climb_target = (relocation.climb_target if relocation else None)
            self._preserve_resume_anchor = bool(recovery_commands)
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._resuming_to_checkpoint = True
            execution.interruption_reason = None
            execution.flight_activity = FlightActivity.SEARCHING
            execution.hold_reason = None
            self._set_state(MissionState.RUNNING)

            return execution

    async def _recover_failed_execution_setup(
        self,
        execution: MissionExecution,
        vehicle: VehicleControl,
        grounded_state: MissionState,
        discard_execution_when_grounded: bool,
        interruption_reason: InterruptionReason | None = None,
    ) -> None:
        """Recover from a Commence/Continue failure without leaving stale setup state."""
        status = vehicle.get_status()

        if status.armed:
            # setup can fail after arming, when the aircraft may still be flying
            # request RTL rather than treating it as back on the ground
            try:
                await self._call_vehicle(vehicle.return_to_launch())
            except MissionExecutionError as recovery_error:
                # if RTL cannot be confirmed, keep the error instead of guessing
                # which state the aircraft ended up in
                self._report_background_error(recovery_error)
                return

            execution.active_target = None
            if interruption_reason is not None:
                execution.interruption_reason = interruption_reason
            self._resuming_to_checkpoint = False
            self._recovery_commands = ()
            self._recovery_index = 0
            self._return_terminal_state = MissionState.INTERRUPTED
            self._background_execution_error = None
            self._set_state(MissionState.RETURNING_HOME)
            return

        # if it never armed, return to the state from before this attempt
        self._return_terminal_state = None
        self._resuming_to_checkpoint = False
        self._recovery_commands = ()
        self._recovery_index = 0
        self._background_execution_error = None

        if discard_execution_when_grounded:
            await self.shutdown()
            # publish the return to planning before clearing the failed execution
            # otherwise reconnecting clients could see an old preflight state
            self._set_state(grounded_state)
            self._current_execution = None
            self._active_target_sent_at_utc = None
            self._reset_terrain_navigation()
            if self._mission_hub is not None:
                self._mission_hub.clear()
        else:
            execution.active_target = None
            self._set_state(grounded_state)

    async def shutdown(self) -> None:
        """Stop the execution monitor during application/test shutdown."""
        # On app shutdown, invalidate the input stream first. The vehicle is
        # separately stopped by bootstrap; here we request BRAKE if reachable.
        if self._manual_epoch is not None or (
            self._current_execution is not None
            and self._current_execution.flight_activity == FlightActivity.MANUAL_CONTROL
        ):
            self._invalidate_manual_channel_locked()
            if self._current_execution is not None:
                self._current_execution.manual_control_ready = False
            if self._vehicle is not None and self._vehicle.get_status().connected:
                try:
                    await self._neutral_if_guided_locked(self._vehicle)
                except MissionExecutionError:
                    logger.warning("Neutral command failed during shutdown; requesting BRAKE")
                try:
                    await self._brake_if_manual_mode_locked(self._vehicle)
                except MissionExecutionError:
                    logger.exception("Could not confirm BRAKE during manual-control shutdown")

        task = self._execution_monitor_task

        if task is None:
            return

        if not task.done():
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._execution_monitor_task = None

    async def _prepare_vehicle_for_search(
        self,
        vehicle: VehicleControl,
        search_altitude_m: float,
    ) -> None:
        if self._rtl_home_altitude_m is not None:
            await self._call_vehicle(vehicle.configure_safe_rtl(self._rtl_home_altitude_m))
        await self._call_vehicle(vehicle.set_guided())

        # arming is only needed when the vehicle reports disarmed
        # the vehicle adapter confirms the change when it sends the command
        if not vehicle.get_status().armed:
            await self._call_vehicle(vehicle.arm())

        await self._call_vehicle(vehicle.takeoff(search_altitude_m))
        await self._wait_for_takeoff_altitude(search_altitude_m)

    # a successful takeoff command does not mean the aircraft reached altitude
    async def _wait_for_takeoff_altitude(self, target_altitude_m: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._takeoff_timeout_s
        version, sample = await self._telemetry_hub.get_latest()

        while True:
            if (
                sample is not None
                and sample.relative_altitude_m
                >= target_altitude_m - self._takeoff_altitude_tolerance_m
            ):
                return

            remaining_s = deadline - loop.time()
            if remaining_s <= 0:
                break

            try:
                version, sample = await asyncio.wait_for(
                    self._telemetry_hub.wait_for_update(version),
                    timeout=remaining_s,
                )
            except TimeoutError:
                break

        raise MissionExecutionError(
            code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
            message="The vehicle did not reach the required takeoff altitude in time.",
            details={
                "target_altitude_m": target_altitude_m,
                "tolerance_m": self._takeoff_altitude_tolerance_m,
            },
        )

    def _start_execution_monitor(
        self,
        execution_id: str,
        telemetry_queue: asyncio.Queue[TelemetrySample],
    ) -> None:
        old_task = self._execution_monitor_task
        if old_task is not None and not old_task.done():
            raise RuntimeError("Mission execution telemetry monitor is already running")

        self._execution_monitor_task = asyncio.create_task(
            self._monitor_execution(
                execution_id=execution_id,
                telemetry_queue=telemetry_queue,
            )
        )

    async def _monitor_execution(
        self,
        execution_id: str,
        telemetry_queue: asyncio.Queue[TelemetrySample],
    ) -> None:
        try:
            while True:
                # record queued positions even while HTTP actions wait for the aircraft
                sample = await telemetry_queue.get()
                execution = self._current_execution
                if execution is None or execution.execution_id != execution_id:
                    return
                self._append_history_sample(execution, sample)

                # drain history in batches, but do not let a backlog stall navigation
                for _ in range(255):
                    try:
                        queued = telemetry_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    self._append_history_sample(execution, queued)

                # keep history after guidance fails, without sending new targets
                if self._background_execution_error is not None:
                    continue

                try:
                    if execution.state in {
                        MissionState.RUNNING, MissionState.PAUSED,
                        MissionState.RETURNING_HOME,
                    }:
                        async with self._action_lock:
                            current = self._current_execution
                            if current is None or current.execution_id != execution_id:
                                return

                            latest = await self._latest_navigation_sample()
                            if latest is None:
                                continue

                            if execution.state in {MissionState.RUNNING, MissionState.PAUSED}:
                                # an unexpected disarm can also happen while paused in BRAKE
                                # report it as a runtime error, not a new mission state
                                vehicle = self._vehicle
                                if vehicle is not None:
                                    status = vehicle.get_status()
                                    if status.connected and status.flight_mode == "RTL":
                                        # ArduPilot may enter RTL independently due
                                        # to its own failsafe. Never send another
                                        # search target or BRAKE over that return.
                                        self._invalidate_manual_channel_locked()
                                        execution.active_target = None
                                        execution.flight_activity = None
                                        execution.hold_reason = None
                                        self._return_terminal_state = MissionState.INTERRUPTED
                                        self._set_state(MissionState.RETURNING_HOME)
                                        continue
                                    if status.connected and not status.armed:
                                        raise MissionExecutionError(
                                            code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                                            message="The vehicle disarmed unexpectedly; mission guidance stopped.",
                                            details={"reason": "UNEXPECTED_DISARM"},
                                        )

                                # drift while paused should never complete a waypoint
                                if execution.state == MissionState.RUNNING:
                                    await self._handle_running_sample(execution, latest)

                            elif (
                                execution.state == MissionState.RETURNING_HOME
                                and self._sample_indicates_landing(latest)
                            ):
                                terminal_state = (
                                    self._return_terminal_state
                                    or MissionState.INTERRUPTED
                                )
                                execution.active_target = None
                                self._set_state(terminal_state)
                                if terminal_state == MissionState.COMPLETED:
                                    return

                    elif execution.state == MissionState.COMPLETED:
                        return

                except MissionExecutionError as error:
                    self._report_background_error(error)
                except Exception:
                    logger.exception("Unexpected mission monitor exception")
                    self._report_background_error(MissionExecutionError(
                        code=MissionExecutionErrorCode.MISSION_MONITOR_FAILED,
                        message="Mission guidance stopped because the background monitor failed.",
                        details={"reason": "UNEXPECTED_MONITOR_ERROR"},
                    ))

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mission telemetry monitor exited unexpectedly")
            self._report_background_error(MissionExecutionError(
                code=MissionExecutionErrorCode.MISSION_MONITOR_FAILED,
                message="Mission monitor stopped unexpectedly; guidance is no longer active.",
                details={"reason": "MONITOR_EXITED"},
            ))
        finally:
            await self._telemetry_hub.unsubscribe(telemetry_queue)

    def _report_background_error(self, error: MissionExecutionError) -> None:
        if self._background_execution_error is not None:
            return
        self._background_execution_error = error
        logger.error("Mission %s guidance stopped: %s (%s)",
                     self._current_execution.execution_id
                     if self._current_execution else "unknown",
                     error.message, error.code.value)
        # publish the error even if the mission still says RUNNING or PAUSED
        # runtimeError tells the frontend that guidance stopped
        self._publish_execution()

    async def _handle_running_sample(
        self,
        execution: MissionExecution,
        sample: TelemetrySample,
    ) -> None:
        if self._terrain_grid is not None:
            await self._handle_terrain_sample(execution, sample)
            return

        target = execution.active_target
        if target is None:
            return
        if not self._target_reached(sample, target):
            self._log_long_target_wait(execution, sample, target)
            return

        vehicle = self._require_ready_vehicle()

        if self._resuming_to_checkpoint:
            checkpoint = execution.resume_checkpoint
            if checkpoint is None or checkpoint.active_target is None:
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="Mission resume state is missing the interrupted target.",
                )

            # reaching the checkpoint is not another completed search waypoint
            await self._call_vehicle(vehicle.go_to(checkpoint.active_target))
            execution.active_target = checkpoint.active_target
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._resuming_to_checkpoint = False
            self._publish_execution()
            return

        logger.info("Mission %s reached waypoint %s", execution.execution_id, len(execution.completed_targets) + 1)
        execution.completed_targets.append(target)
        plan = self._require_current_plan(execution.plan_id)

        next_index = len(execution.completed_targets)

        if next_index < len(plan.route.waypoints):
            next_target = plan.route.waypoints[next_index]
            await self._call_vehicle(vehicle.go_to(next_target))
            execution.active_target = next_target
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._publish_execution()
            return

        # both normal completion and stop use RTL, but only the normal route
        # finishes as COMPLETED after landing
        await self._call_vehicle(vehicle.return_to_launch())

        execution.active_target = None
        execution.flight_activity = None
        execution.hold_reason = None
        execution.manual_control_ready = False
        self._return_terminal_state = MissionState.COMPLETED
        self._set_state(MissionState.RETURNING_HOME)

    def _reset_terrain_navigation(self) -> None:
        self._navigation_commands = ()
        self._navigation_index = 0
        self._navigation_target = None
        self._recovery_commands = ()
        self._recovery_index = 0
        self._preserve_resume_anchor = False
        self._route_departure_anchor = None
        self._recovery_climb_target = None
        self._terrain_launch_origin = None
        self._recovery_origin = None
        self._recovery_segment_ends = ()
        self._last_guidance_target = None
        self._last_guidance_sent_at = None

    @staticmethod
    def _terrain_error(error: ValueError) -> MissionExecutionError:
        return MissionExecutionError(
            code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
            message="Terrain navigation is not ready for this flight.",
            details={"reason": "TERRAIN_PATH_INVALID", "detail": str(error)},
        )

    def _build_internal_commands(
        self, plan: MissionPlan, telemetry: TelemetrySample,
    ) -> tuple[NavigationCommand, ...]:
        if self._terrain_grid is None:
            return ()
        try:
            return build_terrain_navigation(
                self._terrain_grid, plan.route,
                telemetry.latitude_deg, telemetry.longitude_deg,
                plan.planning_settings.search_altitude_m,
                spacing_m=self._terrain_step_m,
            )
        except ValueError as exc:
            raise self._terrain_error(exc) from exc

    def _terrain_takeoff_altitude(
        self, desired_agl_m: float, sample: TelemetrySample,
        first_target: Waypoint | None,
    ) -> float:
        if self._terrain_grid is None:
            return desired_agl_m
        try:
            ground = self._terrain_grid.height_geo(sample.latitude_deg, sample.longitude_deg)
        except ValueError as exc:
            raise self._terrain_error(exc) from exc
        return max(desired_agl_m + ground + 1.0,
                   first_target.altitude_m if first_target is not None else 0.0)

    async def _send_navigation_target(self, vehicle: VehicleControl, target: Waypoint) -> None:
        await self._call_vehicle(vehicle.go_to(target))
        if self._terrain_grid is not None:
            terrain_height = self._terrain_grid.height_geo(target.latitude_deg, target.longitude_deg)
            logger.info(
                "Terrain target lat=%.7f lon=%.7f home_alt=%.2f planned_agl=%.2f",
                target.latitude_deg, target.longitude_deg,
                target.altitude_m, target.altitude_m - terrain_height,
            )

    async def _send_terrain_guidance(
        self, vehicle: VehicleControl, guidance: LookaheadTarget, force: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc)
        previous = self._last_guidance_target
        elapsed = ((now - self._last_guidance_sent_at).total_seconds()
                   if self._last_guidance_sent_at else float("inf"))
        changed = (previous is None or
                   geodesic_distance_m(
                       GeoPoint(previous.latitude_deg, previous.longitude_deg),
                       GeoPoint(guidance.target.latitude_deg, guidance.target.longitude_deg),
                   ) >= 0.7 or
                   abs(previous.altitude_m - guidance.target.altitude_m) >= 0.3)
        if force or elapsed >= 1.0 or (elapsed >= 0.25 and changed):
            await self._send_navigation_target(vehicle, guidance.target)
            self._last_guidance_target = guidance.target
            self._last_guidance_sent_at = now

    async def _guide_recovery(
        self, vehicle: VehicleControl, sample: TelemetrySample, force: bool = False,
    ) -> None:
        if not self._recovery_commands or not self._recovery_segment_ends:
            raise ValueError("Recovery path is missing")
        end = next((i for i in self._recovery_segment_ends
                    if i >= self._recovery_index), None)
        if end is None:
            raise ValueError("Recovery segment is missing")
        previous_end = next((i for i in reversed(self._recovery_segment_ends)
                             if i < self._recovery_index), -1)
        start = (self._recovery_commands[previous_end]
                 if previous_end >= 0 else self._recovery_origin)
        if start is None:
            raise ValueError("Recovery start is missing")
        guidance = terrain_lookahead_target(
            self._terrain_grid, sample.latitude_deg, sample.longitude_deg, start,
            self._recovery_commands[previous_end + 1:end + 1], self._terrain_lookahead_m,
        )
        if guidance.cross_track_m > 8.0:
            raise ValueError("Recovery path deviation exceeds 8 m")
        next_index = previous_end + 1 + guidance.next_index
        if next_index > self._recovery_index:
            self._recovery_index = min(next_index, end)
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
        await self._send_terrain_guidance(vehicle, guidance, force=force)

    async def _guide_search(
        self, execution: MissionExecution, sample: TelemetrySample,
        vehicle: VehicleControl, force: bool = False,
    ) -> None:
        if not self._navigation_commands or self._terrain_launch_origin is None:
            raise ValueError("Terrain search path is missing")
        visible = len(execution.completed_targets)
        first = next((i for i, command in enumerate(self._navigation_commands)
                      if command.visible_index == visible), None)
        if first is None:
            raise ValueError("Terrain search segment is missing")
        end = next(i for i in range(first, len(self._navigation_commands))
                   if self._navigation_commands[i].completes_visible)
        start = (self._navigation_commands[first - 1].target
                 if first else self._terrain_launch_origin)
        guidance = terrain_lookahead_target(
            self._terrain_grid, sample.latitude_deg, sample.longitude_deg, start,
            tuple(command.target for command in self._navigation_commands[first:end + 1]),
            self._terrain_lookahead_m,
        )
        if guidance.cross_track_m > 8.0:
            raise ValueError("Search path deviation exceeds 8 m")
        next_index = first + guidance.next_index
        if next_index > self._navigation_index:
            self._navigation_index = min(next_index, end)
            self._navigation_target = self._navigation_commands[self._navigation_index].target
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
        await self._send_terrain_guidance(vehicle, guidance, force=force)

    async def _handle_terrain_sample(
        self, execution: MissionExecution, sample: TelemetrySample,
    ) -> None:
        grid = self._terrain_grid
        if grid is None:
            return
        plan = self._require_current_plan(execution.plan_id)
        try:
            agl = sample.relative_altitude_m - grid.height_geo(
                sample.latitude_deg, sample.longitude_deg,
            )
        except ValueError as exc:
            await self._call_vehicle(self._require_ready_vehicle().hold())
            execution.flight_activity = FlightActivity.HOLDING
            execution.hold_reason = None
            self._set_state(MissionState.PAUSED)
            raise self._terrain_error(exc) from exc
        climbing_in_place = (
            self._recovery_climb_target is not None
            and self._recovery_commands
            and self._recovery_segment_ends
            and self._recovery_index <= self._recovery_segment_ends[0]
            and self._recovery_origin is not None
            and geodesic_distance_m(
                GeoPoint(sample.latitude_deg, sample.longitude_deg),
                GeoPoint(self._recovery_origin.latitude_deg,
                         self._recovery_origin.longitude_deg),
            ) <= 1.5
            and agl >= 5.0
        )
        if agl < plan.planning_settings.search_altitude_m - 5.0 and not climbing_in_place:
            await self._call_vehicle(self._require_ready_vehicle().hold())
            self._set_state(MissionState.PAUSED)
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                message="Terrain clearance dropped below the allowed margin; vehicle held.",
                details={"reason": "TERRAIN_CLEARANCE_LOW", "agl_m": round(agl, 2)},
            )

        vehicle = self._require_ready_vehicle()
        try:
            if self._recovery_commands:
                end = next(i for i in self._recovery_segment_ends
                           if i >= self._recovery_index)
                if not self._terrain_target_reached(sample, self._recovery_commands[end]):
                    await self._guide_recovery(vehicle, sample)
                    self._log_long_target_wait(execution, sample, self._recovery_commands[end])
                    return
                remaining = tuple(i for i in self._recovery_segment_ends if i > end)
                if remaining:
                    if self._recovery_climb_target is not None and end == self._recovery_segment_ends[0]:
                        self._recovery_climb_target = None
                    self._recovery_index = end + 1
                    self._last_guidance_target = None
                    self._last_guidance_sent_at = None
                    self._active_target_sent_at_utc = datetime.now(timezone.utc)
                    await self._guide_recovery(vehicle, sample, force=True)
                else:
                    self._recovery_commands = ()
                    self._recovery_index = 0
                    self._recovery_segment_ends = ()
                    self._recovery_origin = None
                    self._resuming_to_checkpoint = False
                    self._preserve_resume_anchor = False
                    self._recovery_climb_target = None
                    if self._route_departure_anchor is not None:
                        self._route_departure_anchor = None
                    if self._navigation_target is None:
                        raise ValueError("Unfinished search target missing")
                    self._last_guidance_target = None
                    self._last_guidance_sent_at = None
                    await self._guide_search(execution, sample, vehicle, force=True)
                    execution.active_target = plan.route.waypoints[
                        self._navigation_commands[self._navigation_index].visible_index
                    ]
                    self._publish_execution()
                return

            visible = len(execution.completed_targets)
            end = next(i for i, command in enumerate(self._navigation_commands)
                       if command.visible_index == visible and command.completes_visible)
            if not self._terrain_target_reached(sample, self._navigation_commands[end].target):
                await self._guide_search(execution, sample, vehicle)
                self._log_long_target_wait(execution, sample, self._navigation_commands[end].target)
                return
            execution.completed_targets.append(plan.route.waypoints[visible])
            logger.info("Mission %s reached waypoint %s", execution.execution_id, visible + 1)
            self._navigation_index = end + 1
            if self._navigation_index < len(self._navigation_commands):
                self._navigation_target = self._navigation_commands[self._navigation_index].target
                execution.active_target = plan.route.waypoints[visible + 1]
                self._active_target_sent_at_utc = datetime.now(timezone.utc)
                self._last_guidance_target = None
                self._last_guidance_sent_at = None
                await self._guide_search(execution, sample, vehicle, force=True)
                self._publish_execution()
                return
        except ValueError as exc:
            await self._call_vehicle(vehicle.hold())
            self._set_state(MissionState.PAUSED)
            raise self._terrain_error(exc) from exc

        await self._call_vehicle(vehicle.return_to_launch())
        self._navigation_target = None
        execution.active_target = None
        execution.flight_activity = None
        execution.hold_reason = None
        execution.manual_control_ready = False
        self._return_terminal_state = MissionState.COMPLETED
        self._set_state(MissionState.RETURNING_HOME)

    def _terrain_target_reached(self, sample: TelemetrySample, target: Waypoint) -> bool:
        horizontal = geodesic_distance_m(
            GeoPoint(sample.latitude_deg, sample.longitude_deg),
            GeoPoint(target.latitude_deg, target.longitude_deg),
        )
        return horizontal <= 1.5 and abs(sample.relative_altitude_m - target.altitude_m) <= 0.75

    def _log_long_target_wait(
        self, execution: MissionExecution, sample: TelemetrySample, target: Waypoint
    ) -> None:
        sent_at = self._active_target_sent_at_utc
        if sent_at is None:
            return
        now = datetime.now(timezone.utc)
        if (now - sent_at).total_seconds() < 60:
            return
        if (
            self._last_stall_log_at_utc is not None
            and (now - self._last_stall_log_at_utc).total_seconds() < 30
        ):
            return
        self._last_stall_log_at_utc = now
        distance_m = geodesic_distance_m(
            GeoPoint(latitude_deg=sample.latitude_deg, longitude_deg=sample.longitude_deg),
            GeoPoint(latitude_deg=target.latitude_deg, longitude_deg=target.longitude_deg),
        )
        logger.warning(
            "Mission %s still pursuing target after %.0fs: horizontal_error=%.2fm altitude_error=%.2fm",
            execution.execution_id,
            (now - sent_at).total_seconds(),
            distance_m,
            abs(sample.relative_altitude_m - target.altitude_m),
        )

    async def _latest_navigation_sample(self) -> TelemetrySample | None:
        # history uses queued samples, waypoint checks use the latest position
        # otherwise a busy queue can keep navigation behind the aircraft
        _, sample = await self._telemetry_hub.get_latest()
        if sample is None or self._telemetry_age_s(sample) > self._max_telemetry_age_s:
            return None

        # only samples received after the current target was sent count as arrival
        if (
            self._state == MissionState.RUNNING
            and self._active_target_sent_at_utc is not None
            and sample.timestamp_utc < self._active_target_sent_at_utc
        ):
            return None
        return sample

    # require both horizontal distance and altitude to be within tolerance
    def _target_reached(
        self,
        sample: TelemetrySample,
        target: Waypoint,
    ) -> bool:
        sample_point = GeoPoint(
            latitude_deg=sample.latitude_deg,
            longitude_deg=sample.longitude_deg,
        )
        target_point = GeoPoint(
            latitude_deg=target.latitude_deg,
            longitude_deg=target.longitude_deg,
        )

        horizontal_distance_m = geodesic_distance_m(
            sample_point,
            target_point,
        )
        altitude_error_m = abs(
            sample.relative_altitude_m - target.altitude_m
        )

        return (
            horizontal_distance_m <= self._target_reached_tolerance_m
            and altitude_error_m <= self._target_altitude_tolerance_m
        )

    def _sample_indicates_landing(self, sample: TelemetrySample) -> bool:
        vehicle = self._vehicle
        if vehicle is None:
            return False

        status = vehicle.get_status()

        # low altitude and speed could still be a hover near the ground
        # landing also needs a current heartbeat showing disarmed
        return (
            status.connected
            and not status.armed
            and sample.relative_altitude_m <= self._landing_altitude_tolerance_m
            and sample.ground_speed_mps <= self._landing_speed_tolerance_mps
        )

    async def _require_fresh_telemetry_for_planning(self) -> TelemetrySample:
        _, telemetry = await self._telemetry_hub.get_latest()

        if telemetry is None:
            raise MissionPlanningError(
                code=MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                message="Current vehicle position is unavailable.",
                details={"reason": "NO_TELEMETRY"},
            )

        telemetry_age_s = self._telemetry_age_s(telemetry)

        if telemetry_age_s > self._max_telemetry_age_s:
            raise MissionPlanningError(
                code=MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                message="Current vehicle position is unavailable.",
                details={
                    "reason": "STALE_TELEMETRY",
                    "telemetry_age_seconds": telemetry_age_s,
                    "max_telemetry_age_seconds": self._max_telemetry_age_s,
                },
            )

        return telemetry

    def _validate_execution_telemetry(
        self,
        telemetry: TelemetrySample | None,
    ) -> TelemetrySample:
        if telemetry is None:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                message="Current vehicle position is unavailable.",
                details={"reason": "NO_TELEMETRY"},
            )

        telemetry_age_s = self._telemetry_age_s(telemetry)

        if telemetry_age_s > self._max_telemetry_age_s:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                message="Current vehicle position is unavailable.",
                details={
                    "reason": "STALE_TELEMETRY",
                    "telemetry_age_seconds": telemetry_age_s,
                    "max_telemetry_age_seconds": self._max_telemetry_age_s,
                },
            )

        return telemetry

    def _require_current_plan(self, plan_id: str) -> MissionPlan:
        plan = self._proposed_plan

        if plan is None or plan.plan_id != plan_id:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.PLAN_NOT_CURRENT,
                message="The requested mission plan is not the current proposed plan.",
                details={
                    "requested_plan_id": plan_id,
                    "current_plan_id": plan.plan_id if plan else None,
                },
            )

        if not plan.route.waypoints:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.PLAN_NOT_CURRENT,
                message="The current mission plan does not contain an executable route.",
                details={"current_plan_id": plan.plan_id},
            )

        return plan

    def _require_current_execution(self, execution_id: str) -> MissionExecution:
        execution = self._current_execution

        if execution is None or execution.execution_id != execution_id:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.EXECUTION_NOT_CURRENT,
                message="The requested mission execution is not current.",
                details={
                    "requested_execution_id": execution_id,
                    "current_execution_id": (
                        execution.execution_id if execution else None
                    ),
                },
            )

        return execution

    def _require_camera_ready(self) -> None:
        if self._camera_available is not None and not self._camera_available():
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                message="The camera source is unavailable for a search mission.",
                details={"reason": "CAMERA_UNAVAILABLE"},
            )

    @staticmethod
    def _camera_reason_from_error(error: MissionExecutionError) -> InterruptionReason | None:
        if error.details and error.details.get("reason") == "CAMERA_UNAVAILABLE":
            return "CAMERA_UNAVAILABLE"
        return None

    def _require_ready_vehicle(self) -> VehicleControl:
        vehicle = self._vehicle

        if vehicle is None:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                message="Vehicle control is unavailable.",
                details={"reason": "VEHICLE_CONTROL_UNAVAILABLE"},
            )

        status = vehicle.get_status()
        if not status.connected:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
                message="The vehicle is not ready for mission control.",
                details={"reason": "HEARTBEAT_UNAVAILABLE"},
            )

        return vehicle

    # convert lower-level MAVLink failures into mission errors for the API
    async def _call_vehicle(self, operation: Awaitable[None]) -> None:
        try:
            await operation
        except VehicleCommandError as exc:
            raise MissionExecutionError(
                code=MissionExecutionErrorCode.VEHICLE_COMMAND_FAILED,
                message="The vehicle did not accept or confirm the requested action.",
                details={"reason": str(exc)},
            ) from exc

    def _set_state(self, state: MissionState) -> None:
        self._state = state

        if self._current_execution is not None:
            self._current_execution.state = state
            self._publish_execution()

    def _publish_execution(self) -> None:
        if self._mission_hub is not None and self._current_execution is not None:
            self._mission_hub.publish(
                self._current_execution, runtime_error=self._background_execution_error
            )

    def _raise_action_not_allowed(self, action: str) -> None:
        raise MissionExecutionError(
            code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
            message=f"Mission action '{action}' is not allowed in the current state.",
            details={"current_state": self._state.value, "action": action},
        )

    def _raise_background_error_if_present(self) -> None:
        if self._background_execution_error is not None:
            raise self._background_execution_error

    @staticmethod
    def _append_history_sample(
        execution: MissionExecution,
        sample: TelemetrySample,
    ) -> None:
        # avoid appending the same sequence twice if a sample gets repeated
        if (
            execution.telemetry_history
            and execution.telemetry_history[-1].sequence == sample.sequence
        ):
            return

        execution.telemetry_history.append(sample)

    @staticmethod
    def _telemetry_age_s(telemetry: TelemetrySample) -> float:
        return (
            datetime.now(timezone.utc) - telemetry.timestamp_utc
        ).total_seconds()
