import asyncio
import logging
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
    ) -> None:
        self._telemetry_hub = telemetry_hub
        self._mission_hub = mission_hub
        self._past_executions: list[MissionExecution] = []
        self._active_target_sent_at_utc: datetime | None = None
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

        # route previews have their own lock, mission actions share one lock
        # so pause, stop, and resume cannot overlap
        self._planning_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

        self._execution_monitor_task: asyncio.Task[None] | None = None
        self._return_terminal_state: MissionState | None = None
        self._resuming_to_checkpoint = False
        self._background_execution_error: MissionExecutionError | None = None
        self._last_stall_log_at_utc: datetime | None = None

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

            # only mark RUNNING after GUIDED, arming, takeoff, and first target work
            # before arming, failed setup can return to planning
            # after arming, a failed setup needs an RTL attempt
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
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._set_state(MissionState.RUNNING)

            return execution

    async def pause(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            execution = self._require_current_execution(execution_id)

            if execution.state != MissionState.RUNNING:
                self._raise_action_not_allowed("pause")

            vehicle = self._require_ready_vehicle()

            # BRAKE has to be confirmed before the mission becomes PAUSED
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

            # resend the saved target after switching back from BRAKE to GUIDED
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
            self._set_state(MissionState.RUNNING)

            return execution

    async def stop(self, execution_id: str) -> MissionExecution:
        async with self._action_lock:
            # stop still needs to allow RTL after a guidance error
            execution = self._require_current_execution(execution_id)

            if execution.state not in {MissionState.RUNNING, MissionState.PAUSED}:
                self._raise_action_not_allowed("stop")

            vehicle = self._require_ready_vehicle()
            _, latest = await self._telemetry_hub.get_latest()
            telemetry = self._validate_execution_telemetry(latest)

            # save where the aircraft actually stopped and which search target
            # it was following, continue needs both pieces
            resume_target = execution.active_target

            # during recovery the checkpoint is temporarily the active target
            # stopping again should still remember the unfinished search waypoint
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

            # wait for RTL confirmation before reporting RETURNING_HOME
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

            self._set_state(MissionState.PREFLIGHT_CHECK)
            self._return_terminal_state = None
            self._resuming_to_checkpoint = False
            self._background_execution_error = None

            try:
                await self._prepare_vehicle_for_search(
                    vehicle=vehicle,
                    search_altitude_m=plan.planning_settings.search_altitude_m,
                )

                # fly back to the recorded interruption position first
                # no need to replay the earlier part of the route
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
            self._active_target_sent_at_utc = datetime.now(timezone.utc)
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
            # setup can fail after arming, when the aircraft may still be flying
            # request RTL rather than treating it as back on the ground
            try:
                await self._call_vehicle(vehicle.return_to_launch())
            except MissionExecutionError as recovery_error:
                # if RTL cannot be confirmed, keep the error instead of guessing
                # which state the aircraft ended up in
                self._background_execution_error = recovery_error
                return

            execution.active_target = None
            self._resuming_to_checkpoint = False
            self._return_terminal_state = MissionState.INTERRUPTED
            self._background_execution_error = None
            self._set_state(MissionState.RETURNING_HOME)
            return

        # if it never armed, return to the state from before this attempt
        self._return_terminal_state = None
        self._resuming_to_checkpoint = False
        self._background_execution_error = None

        if discard_execution_when_grounded:
            await self.shutdown()
            # publish the return to planning before clearing the failed execution
            # otherwise reconnecting clients could see an old preflight state
            self._set_state(grounded_state)
            self._current_execution = None
            self._active_target_sent_at_utc = None
            if self._mission_hub is not None:
                self._mission_hub.clear()
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
        self._return_terminal_state = MissionState.COMPLETED
        self._set_state(MissionState.RETURNING_HOME)

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
