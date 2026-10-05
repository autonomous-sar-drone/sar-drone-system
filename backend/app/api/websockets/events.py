from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.schemas.telemetry import telemetry_event_from_sample
from app.runtime.telemetry_hub import TelemetryHub


router = APIRouter()


@router.websocket("/ws/events")
async def events_websocket(websocket: WebSocket) -> None:
    await websocket.accept()

    hub: TelemetryHub = websocket.app.state.telemetry_hub

    try:
        version, sample = await hub.get_latest()

        #send the current telemetry immediately if one already exists
        if sample is not None:
            event = telemetry_event_from_sample(sample)
            await websocket.send_json(event.model_dump(mode="json", by_alias=True))

        last_version = version

        while True:
            version, sample = await hub.wait_for_update(last_version)

            if sample is None:
                continue

            event = telemetry_event_from_sample(sample)

            await websocket.send_json(event.model_dump(mode="json", by_alias=True))

            last_version = version

    except WebSocketDisconnect:
        pass