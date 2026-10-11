from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VehicleStatus:
    connected: bool
    armed: bool
    flight_mode: str | None
    last_heartbeat_utc: datetime | None