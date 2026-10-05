from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TelemetrySample:
    sequence: int
    timestamp_utc: datetime
    latitude_deg: float
    longitude_deg: float
    relative_altitude_m: float
    heading_deg: float | None
    ground_speed_mps: float
