"""Backend-only camera loss watchdog; it never sends MAVLink commands itself."""
import asyncio
import logging
import math

from app.domain.models.mission_state import MissionState
from app.mission.errors import MissionExecutionError
from app.mission.mission_service import MissionService
from app.vision.vision_runtime import VisionRuntime

LOG = logging.getLogger(__name__)


class CameraSafetyWatchdog:
    def __init__(
        self,
        vision: VisionRuntime,
        mission: MissionService,
        *,
        outage_after_s: float = 5.0,
        poll_period_s: float = 0.2,
    ) -> None:
        if (
            not math.isfinite(outage_after_s)
            or not math.isfinite(poll_period_s)
            or not math.isfinite(vision.stale_after_s)
            or outage_after_s <= vision.stale_after_s
            or poll_period_s <= 0
        ):
            raise ValueError("camera RTL threshold must exceed source-stale threshold; poll must be positive and finite")
        self.vision = vision
        self.mission = mission
        self.outage_after_s = outage_after_s
        self.poll_period_s = poll_period_s
        self._tripped_for_outage = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("camera watchdog already started")
        self._task = asyncio.create_task(self._run(), name="camera-safety-watchdog")

    async def stop(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                LOG.exception("Camera watchdog check failed unexpectedly")
            await asyncio.sleep(self.poll_period_s)

    async def check_once(self) -> None:
        """One deterministic check; separately testable without background timers."""
        if self.vision.camera_ready():
            # A fresh source-frame arrival ends the outage episode.
            self._tripped_for_outage = False
            self.vision.set_automatic_rtl_triggered(False)
            return

        if self._tripped_for_outage:
            return
        execution = self.mission.current_execution
        if execution is None or execution.state not in {MissionState.RUNNING, MissionState.PAUSED}:
            return
        elapsed_s = self.vision.camera_source_age_s()
        if elapsed_s is None or elapsed_s < self.outage_after_s:
            return

        # Latch before awaiting the locked mission action. Never spam an
        # ambiguous MAVLink command after a timeout/failed acknowledgement.
        self._tripped_for_outage = True
        try:
            attempted = await self.mission.interrupt_due_to_camera_loss()
        except MissionExecutionError as error:
            # MissionService publishes the runtime error, without claiming RTL.
            self.vision.set_automatic_rtl_triggered(True)
            LOG.error("Camera-loss RTL not confirmed: %s (%s)", error.message, error.code.value)
            return
        if attempted:
            self.vision.set_automatic_rtl_triggered(True)
        else:
            # A simultaneous operator Stop may have won the action lock.
            self._tripped_for_outage = False
