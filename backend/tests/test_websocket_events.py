from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.websockets.events import router as events_router
from app.domain.models.telemetry import TelemetrySample
from app.runtime.telemetry_hub import TelemetryHub


def make_sample() -> TelemetrySample:
    return TelemetrySample(
        sequence=1,
        timestamp_utc=datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc),
        latitude_deg=30.0,
        longitude_deg=-97.0,
        relative_altitude_m=25.0,
        heading_deg=90.0,
        ground_speed_mps=5.0
    )


@asynccontextmanager
async def websocket_lifespan(app: FastAPI) -> AsyncIterator[None]:
    hub = TelemetryHub()

    #put telemetry in the hub before the WebSocket client connects
    await hub.publish(make_sample())

    app.state.telemetry_hub = hub

    yield


websocket_app = FastAPI(lifespan=websocket_lifespan)
websocket_app.include_router(events_router)


def test_websocket_sends_latest_telemetry() -> None:
    with TestClient(websocket_app) as client:
        with client.websocket_connect("/ws/events") as websocket:
            payload = websocket.receive_json()

    assert payload["type"] == "telemetry_update"
    assert payload["data"]["timestamp"] == "2026-09-30T12:00:00Z"
    assert payload["data"]["latitudeDeg"] == 30.0
    assert payload["data"]["longitudeDeg"] == -97.0
    assert payload["data"]["relativeAltitudeM"] == 25.0
    assert payload["data"]["headingDeg"] == 90.0
    assert payload["data"]["groundSpeedMps"] == 5.0