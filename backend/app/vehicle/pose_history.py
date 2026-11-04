"""Bounded MAVLink capture-time pose history, internal to the backend.

The camera timestamp and MAVLink receive timestamps share system UTC on the
simulator host. This is approximate synchronization, NOT hardware clock sync.
Message boot times are checked to avoid pairing unrelated position/attitude.
"""
from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class AircraftPose:
    latitude_deg: float
    longitude_deg: float
    relative_altitude_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float


@dataclass(frozen=True, slots=True)
class _Position:
    at_unix_ms: int
    boot_ms: int
    lat: float
    lon: float
    alt_m: float


@dataclass(frozen=True, slots=True)
class _Attitude:
    at_unix_ms: int
    boot_ms: int
    roll: float
    pitch: float
    yaw: float


class PoseHistory:
    def __init__(self, *, position_max_age_ms: int = 450,
                 attitude_max_age_ms: int = 200,
                 boot_skew_ms: int = 450, limit: int = 256):
        if min(position_max_age_ms, attitude_max_age_ms, boot_skew_ms, limit) <= 0:
            raise ValueError("invalid pose history limits")
        self.position_max_age_ms = position_max_age_ms
        self.attitude_max_age_ms = attitude_max_age_ms
        self.boot_skew_ms = boot_skew_ms
        self._positions: deque[_Position] = deque(maxlen=limit)
        self._attitudes: deque[_Attitude] = deque(maxlen=limit)

    def add_position(self, message: Any, *, at_unix_ms: int | None = None) -> None:
        if not hasattr(message, "time_boot_ms"):
            return
        try:
            fix = _Position(
                at_unix_ms if at_unix_ms is not None else time.time_ns() // 1_000_000,
                int(message.time_boot_ms), float(message.lat) / 1e7,
                float(message.lon) / 1e7, float(message.relative_alt) / 1000,
            )
            if (fix.boot_ms < 0 or not all(map(math.isfinite, (fix.lat, fix.lon, fix.alt_m)))
                    or not -90 <= fix.lat <= 90 or not -180 <= fix.lon <= 180):
                return
        except (TypeError, ValueError, AttributeError):
            return
        self._positions.append(fix)

    def add_attitude(self, message: Any, *, at_unix_ms: int | None = None) -> None:
        try:
            att = _Attitude(
                at_unix_ms if at_unix_ms is not None else time.time_ns() // 1_000_000,
                int(message.time_boot_ms), float(message.roll),
                float(message.pitch), float(message.yaw),
            )
            if att.boot_ms < 0 or not all(map(math.isfinite, (att.roll, att.pitch, att.yaw))):
                return
        except (TypeError, ValueError, AttributeError):
            return
        self._attitudes.append(att)

    def at_capture(self, capture_unix_ms: int) -> AircraftPose | None:
        if not self._positions or not self._attitudes:
            return None
        # In a tie, prefer the more recently received sample.
        pos = min(reversed(self._positions), key=lambda sample: abs(sample.at_unix_ms - capture_unix_ms))
        att = min(reversed(self._attitudes), key=lambda sample: abs(sample.at_unix_ms - capture_unix_ms))
        if (abs(pos.at_unix_ms - capture_unix_ms) > self.position_max_age_ms
                or abs(att.at_unix_ms - capture_unix_ms) > self.attitude_max_age_ms
                or abs(pos.boot_ms - att.boot_ms) > self.boot_skew_ms):
            return None
        # Require a reasonable height above the flat home-ground plane.
        if not 0 < pos.alt_m <= 500:
            return None
        return AircraftPose(pos.lat, pos.lon, pos.alt_m, att.roll, att.pitch, att.yaw)
