import asyncio

from app.domain.models.telemetry import TelemetrySample


class TelemetryHub:
    def __init__(self) -> None:
        # The hub keeps the newest sample for state-style consumers such as the
        # WebSocket and route planner. Mission execution subscribes separately
        # when it needs every sample rather than only the latest one.
        self._latest: TelemetrySample | None = None
        self._version: int = 0
        self._condition = asyncio.Condition()
        self._subscribers: set[asyncio.Queue[TelemetrySample]] = set()

    async def publish(self, sample: TelemetrySample) -> None:
        async with self._condition:
            self._latest = sample
            self._version += 1

            # Subscription queues are intentionally unbounded for the current
            # slice so an active MissionExecution can preserve every received
            # telemetry sample. Durable/downsampled storage comes later.
            for queue in tuple(self._subscribers):
                queue.put_nowait(sample)

            # Wake consumers that only care that a newer latest sample exists.
            self._condition.notify_all()

    async def get_latest(self) -> tuple[int, TelemetrySample | None]:
        # One-time fetch of the current version and telemetry state.
        async with self._condition:
            return self._version, self._latest

    async def wait_for_update(
        self,
        after_version: int,
    ) -> tuple[int, TelemetrySample | None]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._version > after_version
            )

            return self._version, self._latest

    async def subscribe(
        self,
    ) -> tuple[asyncio.Queue[TelemetrySample], TelemetrySample | None]:
        """Subscribe to every future sample and atomically return the latest one."""
        async with self._condition:
            queue: asyncio.Queue[TelemetrySample] = asyncio.Queue()
            self._subscribers.add(queue)

            # Registering and reading latest under the same condition prevents
            # a publish from slipping between those two operations.
            return queue, self._latest

    async def unsubscribe(
        self,
        queue: asyncio.Queue[TelemetrySample],
    ) -> None:
        async with self._condition:
            self._subscribers.discard(queue)
