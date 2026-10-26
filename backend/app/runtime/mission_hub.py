import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import Waypoint
from app.mission.errors import MissionExecutionError


@dataclass(frozen=True)
class MissionRuntimeError:
    code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True)
class MissionSnapshot:
    timestamp_utc: datetime
    execution_id: str
    plan_id: str
    state: MissionState
    active_target: Waypoint | None
    runtime_error: MissionRuntimeError | None = None


class MissionHub:
    """Broadcast snapshot copies, not references to mutable executions."""

    def __init__(self) -> None:
        self._latest: MissionSnapshot | None = None
        self._subscribers: set[asyncio.Queue[MissionSnapshot]] = set()

    # the latest snapshot is saved for new clients and queued for current listeners
    def publish(
        self, execution: MissionExecution, runtime_error: MissionExecutionError | None = None
    ) -> None:
        # capture the execution now, otherwise queued events could reflect later changes
        snapshot = MissionSnapshot(
            timestamp_utc=datetime.now(timezone.utc),
            execution_id=execution.execution_id,
            plan_id=execution.plan_id,
            state=execution.state,
            active_target=execution.active_target,
            # copy error details too, since the original dictionary can still change
            runtime_error=MissionRuntimeError(
                code=runtime_error.code.value,
                message=runtime_error.message,
                details=dict(runtime_error.details or {}),
            ) if runtime_error is not None else None,
        )
        self._latest = snapshot
        for queue in tuple(self._subscribers):
            queue.put_nowait(snapshot)

    def subscribe(
        self,
    ) -> tuple[asyncio.Queue[MissionSnapshot], MissionSnapshot | None]:
        # subscribe and read latest together, so reconnects do not miss a state
        queue: asyncio.Queue[MissionSnapshot] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue, self._latest

    def clear(self) -> None:
        # old mission states should not reappear when the frontend reconnects
        self._latest = None

    def unsubscribe(self, queue: asyncio.Queue[MissionSnapshot]) -> None:
        self._subscribers.discard(queue)
