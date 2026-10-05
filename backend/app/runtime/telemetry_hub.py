import asyncio
from app.domain.models.telemetry import TelemetrySample


class TelemetryHub:
    def __init__(self) -> None:
        #latest shared telemetry state, starts empty until Vehicle publishes
        self._latest: TelemetrySample | None = None
        self._version: int = 0

        #used to safely access shared state and wake consumers on an update
        self._condition: asyncio.Condition = asyncio.Condition()

    async def publish(self, sample: TelemetrySample) -> None:
        async with self._condition:
            self._latest = sample
            self._version += 1

            #wake all consumers currently waiting for newer telemetry
            self._condition.notify_all()

    async def get_latest(self) -> tuple[int, TelemetrySample | None]:
        #one time fetch of the current version and telemetry state
        async with self._condition:
            return self._version, self._latest

    async def wait_for_update(self, after_version: int) -> tuple[int, TelemetrySample | None]:
        async with self._condition:
            #sleep until telemetry has been published past the version this consumer last saw
            await self._condition.wait_for(lambda: self._version > after_version)

            return self._version, self._latest