from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from app.domain.models.telemetry import TelemetrySample


class TelemetryData(BaseModel):
    timestamp: datetime
    latitude_deg: float = Field(serialization_alias="latitudeDeg")
    longitude_deg: float = Field(serialization_alias="longitudeDeg")
    relative_altitude_m: float = Field(serialization_alias="relativeAltitudeM")
    heading_deg: float | None = Field(serialization_alias="headingDeg")
    ground_speed_mps: float = Field(serialization_alias="groundSpeedMps")


class TelemetryEvent(BaseModel):
    type: Literal["telemetry_update"] = "telemetry_update"
    data: TelemetryData


def telemetry_event_from_sample(sample: TelemetrySample) -> TelemetryEvent:
    return TelemetryEvent(
        data=TelemetryData(
            timestamp=sample.timestamp_utc,
            latitude_deg=sample.latitude_deg,
            longitude_deg=sample.longitude_deg,
            relative_altitude_m=sample.relative_altitude_m,
            heading_deg=sample.heading_deg,
            ground_speed_mps=sample.ground_speed_mps
        )
    )