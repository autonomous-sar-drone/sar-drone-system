from datetime import datetime, timezone

from app.api.schemas.telemetry import telemetry_event_from_sample
from app.domain.models.telemetry import TelemetrySample


def test_telemetry_schema() -> None:
    telemetry_sample = TelemetrySample(
        sequence=1,
        timestamp_utc=datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc),
        latitude_deg=30.0,
        longitude_deg=-97.0,
        relative_altitude_m=25.0,
        heading_deg=None,
        ground_speed_mps=5.0
    )

    telemetry_event = telemetry_event_from_sample(telemetry_sample)

    assert telemetry_event.type == "telemetry_update"
    assert telemetry_event.data.latitude_deg == 30.0
    assert telemetry_event.data.heading_deg is None

    payload = telemetry_event.model_dump(
        mode="json",
        by_alias=True
    )

    assert payload["type"] == "telemetry_update"
    assert payload["data"]["timestamp"] == "2026-09-30T12:00:00Z"
    assert payload["data"]["latitudeDeg"] == 30.0
    assert payload["data"]["longitudeDeg"] == -97.0
    assert payload["data"]["relativeAltitudeM"] == 25.0
    assert payload["data"]["headingDeg"] is None
    assert payload["data"]["groundSpeedMps"] == 5.0