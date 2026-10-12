import asyncio
from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from app.domain.models.mission_execution import MissionExecution, ResumeCheckpoint
from app.domain.models.mission_plan import MissionPlan
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference, PlanningSettings, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea
from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.geospatial.distance import geodesic_distance_m
from app.geospatial.route_planning import generate_route
from app.geospatial.search_area_validation import validate_search_area
from app.mission.errors import (
    MissionExecutionError,
    MissionExecutionErrorCode,
    MissionPlanningError,
    MissionPlanningErrorCode,
)
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.errors import VehicleCommandError


class VehicleControl(Protocol):
    """Application-level vehicle operations MissionService is allowed to use."""

    def get_status(self) -> VehicleStatus: ...

    async def set_guided(self) -> None: ...

    async def arm(self) -> None: ...

    async def takeoff(self, altitude_m: float) -> None: ...

    async def go_to(self, target: Waypoint) -> None: ...

    async def hold(self) -> None: ...

    async def return_to_launch(self) -> None: ...


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
    ) -> None:
        self._telemetry_hub = telemetry_hub
        self._default_planning_settings = default_planning_settings
        self._max_telemetry_age_s = max_telemetry_age_s
        self._max_transit_distance_m = max_transit_distance_m
        self._max_route_distance_m = max_route_distance_m
        self._vehicle = vehicle

        self._target_reached_tolerance_m = target_reached_tolerance_m
        self._target_altitude_tolerance_m = target_altitude_tolerance_m
        self._takeoff_altitude_tolerance_m = takeoff_altitude_tolerance_m
        self._takeoff_timeout_s = takeoff_timeout_s
        self._landing_altitude_tolerance_m = landing_altitude_tolerance_m
        self._landing_speed_tolerance_mps = landing_speed_tolerance_mps

        self._state = MissionState.IDLE
        self._proposed_plan: MissionPlan | None = None
        self._current_execution: MissionExecution | None = None

        # Planning and execution actions are separate concerns, but execution
        # actions themselves are serialized so Pause/Stop/Resume cannot race.
        self._planning_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

        self._execution_monitor_task: asyncio.Task[None] | None = None
        self._return_terminal_state: MissionState | None = None
        self._resuming_to_checkpoint = False
        self._background_execution_error: MissionExecutionError | None = None

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
    def background_execution_error(self) -> MissionExecutionError | None:
        return self._background_execution_error

    async def preview_route(
        self,
        search_area: SearchArea,
        planning_settings: PlanningSettings | None = None,
    ) -> MissionPlan:
        # Only one preview can replace the proposed plan at a time.
        async with self._planning_lock:
            if self._state not in {MissionState.IDLE, MissionState.PLANNING}:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="A route preview cannot be generated in the current mission state.",
                    details={"current_state": self._state.value},
                )

            # A new preview attempt makes any older proposed plan stale.
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

            # Subscribe before reading latest telemetry so no sample can fall
            # through a gap between initial history capture and monitoring.
            telemetry_queue, latest = await self._telemetry_hub.subscribe()

            try:
                telemetry = self._validate_execution_telemetry(latest)
            except MissionExecutionError:
                await self._telemetry_hub.unsubscribe(telemetry_queue)
                raise

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

            self._start_execution_monitor(
                execution_id=execution.execution_id,
                telemetry_queue=telemetry_queue,
            )

            # Commence does not report RUNNING until mode, arming, takeoff, and
            # the first GUIDED target have all succeeded. If setup fails before
            # arming, the proposed plan remains available for another attempt.
            # Once armed, recovery first tries to return the aircraft safely.
            try:
                await self._prepare_vehicle_for_search(
                    vehicle=vehicle,
                    search_altitude_m=plan.planning_settings.search_altitude_m,
                )

                first_target = plan.route.waypoints[0]
                await self._call_vehicle(vehicle.go_to(first_target))
            except MissionExecutionError:
                await self._recover_failed_execution_setup(
                    execution=execution,
                    vehicle=vehicle,
                    grounded_state=MissionState.PLANNING,
                    discard_execution_when_grounded=True,
                )
                raise

            execution.active_target = first_target
            self._set_state(MissionState.RUNNING)

            return execution

    async def pause(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.RUNNING:
                self._raise_action_not_allowed("pause")

            vehicle = self._require_ready_vehicle()

            # Do not claim PAUSED until the vehicle reports the LOITER mode that
            # implements our hold-position behavior.
            await self._call_vehicle(vehicle.hold())
            self._set_state(MissionState.PAUSED)

            return execution

    async def resume(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            self._raise_background_error_if_present()
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.PAUSED:
                self._raise_action_not_allowed("resume")

            if execution.active_target is None:
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="The paused mission has no active target to resume.",
                    details={"current_state": execution.state.value},
                )

            vehicle = self._require_ready_vehicle()

            await self._call_vehicle(vehicle.set_guided())
            await self._call_vehicle(vehicle.go_to(execution.active_target))

            # The same target is deliberately resent after leaving LOITER.
            self._set_state(MissionState.RUNNING)

            return execution

    async def stop(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            # Stop is a recovery/safety action. A prior background guidance
            # error must not prevent the operator from attempting RTL.
            execution = self._require_current_execution(execution_id)

            if execution.state not in {MissionState.RUNNING, MissionState.PAUSED}:
                self._raise_action_not_allowed("stop")

            vehicle = self._require_ready_vehicle()
            _, latest = await self._telemetry_hub.get_latest()
            telemetry = self._validate_execution_telemetry(latest)

            # Capture the actual aircraft position immediately before leaving
            # search behavior. The target being pursued is preserved separately
            # so Continue can return to this point and then resume that target.
            resume_target = execution.active_target

            # While Continue is flying back to an earlier checkpoint, the
            # temporary active target is that checkpoint. A second Stop must
            # still remember the real search target that should resume later.
            if (
                self._resuming_to_checkpoint
                and execution.resume_checkpoint is not None
                and execution.resume_checkpoint.active_target is not None
            ):
                resume_target = execution.resume_checkpoint.active_target

            execution.resume_checkpoint = ResumeCheckpoint(
                telemetry=telemetry,
                active_target=resume_target,
            )

            # Important: the mission remains RUNNING/PAUSED while RTL is only a
            # request. MavlinkVehicle returns after reported RTL confirmation.
            await self._call_vehicle(vehicle.return_to_launch())

            execution.active_target = None
            self._resuming_to_checkpoint = False
            self._return_terminal_state = MissionState.INTERRUPTED
            self._background_execution_error = None
            self._set_state(MissionState.RETURNING_HOME)

            return execution

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
            self._validate_execution_telemetry(latest)

            # The telemetry monitor intentionally remains alive while the
            # execution is INTERRUPTED. This preserves the continuous in-memory
            # history and means Continue does not create a second subscriber.
            if (
                self._execution_monitor_task is None
                or self._execution_monitor_task.done()
            ):
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="The interrupted mission is no longer being monitored.",
                    details={"current_state": execution.state.value},
                )

            self._set_state(MissionState.PREFLIGHT_CHECK)
            self._return_terminal_state = None
            self._resuming_to_checkpoint = False
            self._background_execution_error = None

            try:
                await self._prepare_vehicle_for_search(
                    vehicle=vehicle,
                    search_altitude_m=plan.planning_settings.search_altitude_m,
                )

                # Resume uses the exact interrupted horizontal position rather
                # than replaying a potentially long route segment from its
                # prior start.
                checkpoint_target = Waypoint(
                    latitude_deg=checkpoint.telemetry.latitude_deg,
                    longitude_deg=checkpoint.telemetry.longitude_deg,
                    altitude_m=checkpoint.telemetry.relative_altitude_m,
                    altitude_reference=AltitudeReference.HOME_RELATIVE,
                )

                await self._call_vehicle(vehicle.go_to(checkpoint_target))
            except MissionExecutionError:
                await self._recover_failed_execution_setup(
                    execution=execution,
                    vehicle=vehicle,
                    grounded_state=MissionState.INTERRUPTED,
                    discard_execution_when_grounded=False,
                )
                raise

            execution.active_target = checkpoint_target
            self._resuming_to_checkpoint = True
            self._set_state(MissionState.RUNNING)

            return execution

    async def _recover_failed_execution_setup(
        self,
        execution: MissionExecution,
        vehicle: VehicleControl,
        grounded_state: MissionState,
        discard_execution_when_grounded: bool,
    ) -> None:
        """Recover from a Commence/Continue failure without leaving stale setup state."""
        status = vehicle.get_status()

        if status.armed:
            # A setup failure after arming may leave the aircraft airborne or
            # ready to move. Prefer a confirmed RTL over pretending the failed
            # execution safely returned to its prior application state.
            try:
                await self._call_vehicle(vehicle.return_to_launch())
            except MissionExecutionError as recovery_error:
                # No stronger state claim is safe when RTL itself cannot be
                # confirmed. Preserve the recovery error for later diagnosis.
                self._background_execution_error = recovery_error
                return

            execution.active_target = None
            self._resuming_to_checkpoint = False
            self._return_terminal_state = MissionState.INTERRUPTED
            self._background_execution_error = None
            self._set_state(MissionState.RETURNING_HOME)
            return

        # No armed aircraft needs recovery, so restore the stable state that
        # existed before this setup attempt.
        self._return_terminal_state = None
        self._resuming_to_checkpoint = False
        self._background_execution_error = None

        if discard_execution_when_grounded:
            await self.shutdown()
            self._current_execution = None
            self._state = grounded_state
        else:
            execution.active_target = None
            self._set_state(grounded_state)

    async def shutdown(self) -> None:
        """Stop the execution monitor during application/test shutdown."""
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
        await self._call_vehicle(vehicle.set_guided())

        # Avoid sending a redundant arm command when the reported state already
        # says armed. MavlinkVehicle itself still verifies the state transition
        # when arming is necessary.
        if not vehicle.get_status().armed:
            await self._call_vehicle(vehicle.arm())

        await self._call_vehicle(vehicle.takeoff(search_altitude_m))
        await self._wait_for_takeoff_altitude(search_altitude_m)

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
                sample = await telemetry_queue.get()

                execution = self._current_execution
                if execution is None or execution.execution_id != execution_id:
                    return

                # History collection is intentionally outside the action lock.
                # If a Pause/Stop call waits on vehicle confirmation, incoming
                # telemetry continues to be consumed and preserved instead of
                # collapsing down to TelemetryHub's latest sample.
                self._append_history_sample(execution, sample)

                # Once background guidance fails, keep collecting the real
                # telemetry trail but stop issuing additional autonomous commands.
                # The next operator action surfaces the saved error.
                if self._background_execution_error is not None:
                    continue

                try:
                    if execution.state == MissionState.RUNNING:
                        async with self._action_lock:
                            # The state may have changed while waiting for the lock.
                            if execution.state == MissionState.RUNNING:
                                await self._handle_running_sample(execution, sample)

                    elif execution.state == MissionState.RETURNING_HOME:
                        async with self._action_lock:
                            if (
                                execution.state == MissionState.RETURNING_HOME
                                and self._sample_indicates_landing(sample)
                            ):
                                terminal_state = self._return_terminal_state
                                if terminal_state is None:
                                    terminal_state = MissionState.INTERRUPTED

                                self._set_state(terminal_state)
                                execution.active_target = None

                                # Interrupted missions remain monitored because the
                                # same execution may later Continue. A completed
                                # mission has no continuation path in this slice.
                                if terminal_state == MissionState.COMPLETED:
                                    return

                    elif execution.state == MissionState.COMPLETED:
                        return

                except MissionExecutionError as error:
                    # Background guidance has no HTTP caller to receive an error.
                    # Save it for the next operator action and keep raw history.
                    self._background_execution_error = error

        except asyncio.CancelledError:
            raise
        finally:
            await self._telemetry_hub.unsubscribe(telemetry_queue)

    async def _handle_running_sample(
        self,
        execution: MissionExecution,
        sample: TelemetrySample,
    ) -> None:
        target = execution.active_target
        if target is None or not self._target_reached(sample, target):
            return

        vehicle = self._require_ready_vehicle()

        if self._resuming_to_checkpoint:
            checkpoint = execution.resume_checkpoint
            if checkpoint is None or checkpoint.active_target is None:
                raise MissionExecutionError(
                    code=MissionExecutionErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="Mission resume state is missing the interrupted target.",
                )

            # Reaching the checkpoint is not completing a planned search target.
            # It only restores the aircraft to the exact interruption point.
            await self._call_vehicle(vehicle.go_to(checkpoint.active_target))
            execution.active_target = checkpoint.active_target
            self._resuming_to_checkpoint = False
            return

        execution.completed_targets.append(target)
        plan = self._require_current_plan(execution.plan_id)

        next_index = len(execution.completed_targets)

        if next_index < len(plan.route.waypoints):
            next_target = plan.route.waypoints[next_index]
            await self._call_vehicle(vehicle.go_to(next_target))
            execution.active_target = next_target
            return

        # The final search target is complete. Normal completion and operator
        # Stop share RTL physically but end in different terminal states.
        await self._call_vehicle(vehicle.return_to_launch())

        execution.active_target = None
        self._return_terminal_state = MissionState.COMPLETED
        self._set_state(MissionState.RETURNING_HOME)

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

        # Low altitude and speed alone can also describe a near-ground hover.
        # Require a current heartbeat reporting DISARMED before declaring the
        # RTL/landing sequence finished.
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
        # A subscription normally delivers each sample once. Keep a small
        # guard here so an accidentally repeated publication does not duplicate
        # the immediately previous history point.
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
