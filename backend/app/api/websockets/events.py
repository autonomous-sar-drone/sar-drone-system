import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.schemas.telemetry import telemetry_event_from_sample
from app.api.schemas.mission_execution import mission_update_event_from_snapshot
from app.runtime.mission_hub import MissionHub
from app.runtime.telemetry_hub import TelemetryHub


router = APIRouter()


@router.websocket("/ws/events")
async def events_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    telemetry_hub: TelemetryHub = websocket.app.state.telemetry_hub
    mission_hub: MissionHub | None = getattr(
        websocket.app.state, "mission_hub", None
    )
    # the mission hub may be absent in older telemetry-only test setups
    mission_queue = None
    if mission_hub is not None:
        mission_queue, mission_latest = mission_hub.subscribe()
    else:
        mission_latest = None

    pending: set[asyncio.Task] = set()
    disconnect_wait: asyncio.Task | None = None
    try:
        # send the latest known data first, so reconnecting clients catch up
        telemetry_version, telemetry_latest = await telemetry_hub.get_latest()
        if telemetry_latest is not None:
            event = telemetry_event_from_sample(telemetry_latest)
            await websocket.send_json(event.model_dump(mode="json", by_alias=True, exclude_none=True))

        if mission_latest is not None:
            event = mission_update_event_from_snapshot(mission_latest)
            await websocket.send_json(event.model_dump(mode="json", by_alias=True, exclude_none=True))

        # incoming messages are only used to detect disconnects
        # mission commands still go through the HTTP API
        disconnect_wait = asyncio.create_task(websocket.receive())

        while True:
            telemetry_wait = asyncio.create_task(
                telemetry_hub.wait_for_update(telemetry_version)
            )
            pending = {telemetry_wait}
            mission_wait = None
            if mission_queue is not None:
                mission_wait = asyncio.create_task(mission_queue.get())
                pending.add(mission_wait)

            # one socket carries both event types, whichever changes first gets sent
            done, remaining = await asyncio.wait(
                pending | {disconnect_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            pending = remaining - {disconnect_wait}
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            pending = set()

            if disconnect_wait in done:
                message = disconnect_wait.result()
                if message["type"] == "websocket.disconnect":
                    break
                disconnect_wait = asyncio.create_task(websocket.receive())

            if telemetry_wait in done:
                telemetry_version, sample = telemetry_wait.result()
                if sample is not None:
                    event = telemetry_event_from_sample(sample)
                    await websocket.send_json(
                        event.model_dump(mode="json", by_alias=True, exclude_none=True)
                    )

            if mission_wait is not None and mission_wait in done:
                event = mission_update_event_from_snapshot(mission_wait.result())
                await websocket.send_json(
                    event.model_dump(mode="json", by_alias=True, exclude_none=True)
                )

    except WebSocketDisconnect:
        pass
    finally:
        # cancel outstanding waits and drop the subscription on disconnect
        for task in pending:
            task.cancel()
        if disconnect_wait is not None and not disconnect_wait.done():
            disconnect_wait.cancel()
        await asyncio.gather(
            *pending,
            *([disconnect_wait] if disconnect_wait is not None else []),
            return_exceptions=True,
        )
        if mission_hub is not None and mission_queue is not None:
            mission_hub.unsubscribe(mission_queue)
