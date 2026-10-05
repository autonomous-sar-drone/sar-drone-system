import asyncio

from app.core.config import MAVLINK_ENDPOINT
from app.runtime.telemetry_hub import TelemetryHub
from app.vehicle.mavlink_telemetry import (
    create_mavlink_connection,
    initialize_mavlink_telemetry,
    run_mavlink_telemetry
)


async def main() -> None:
    hub = TelemetryHub()
    connection = create_mavlink_connection(MAVLINK_ENDPOINT)
    telemetry_task: asyncio.Task[None] | None = None

    try:
        print(f"Connecting to MAVLink at {MAVLINK_ENDPOINT}...")

        await initialize_mavlink_telemetry(connection)

        print("Heartbeat received and telemetry stream requested.")

        telemetry_task = asyncio.create_task(
            run_mavlink_telemetry(hub, connection)
        )

        version, sample = await asyncio.wait_for(
            hub.wait_for_update(0),
            timeout=10.0
        )

        if sample is None:
            raise RuntimeError("TelemetryHub updated without a telemetry sample")

        print(f"Received telemetry version {version}:")
        print(sample)

    finally:
        if telemetry_task is not None:
            telemetry_task.cancel()

            try:
                await telemetry_task
            except asyncio.CancelledError:
                pass

        connection.close()


if __name__ == "__main__":
    asyncio.run(main())