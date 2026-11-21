"""Server-side validation and terrain envelope for operator velocity intent.

This is a per-takeover safety gate. MissionService will own the gate and its
watchdog in the manual-control integration; it is deliberately not a second
MAVLink reader or an alternate autonomous navigation producer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.geospatial.terrain_grid import TerrainGrid


@dataclass(frozen=True)
class ManualAxes:
    forward: int
    right: int
    up: int
    yaw: int

    @classmethod
    def from_wire(cls, values: Mapping[str, Any]) -> ManualAxes:
        axes = tuple(values.get(key) for key in ("forward", "right", "up", "yaw"))
        if any(type(axis) is not int or axis not in (-1, 0, 1) for axis in axes):
            raise ValueError("Manual axes must be integers -1, 0, or 1")
        return cls(*axes)

    @property
    def neutral(self) -> bool:
        return self == ManualAxes(0, 0, 0, 0)


@dataclass(frozen=True)
class ManualInputFrame:
    execution_id: str
    control_epoch: str
    sequence: int
    client_sent_at_ms: int
    axes: ManualAxes

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> ManualInputFrame:
        execution_id = data.get("executionId")
        control_epoch = data.get("controlEpoch")
        sequence = data.get("sequence")
        sent_ms = data.get("clientSentAtMs")
        if (not isinstance(execution_id, str) or not 0 < len(execution_id) <= 128
                or not isinstance(control_epoch, str) or not 0 < len(control_epoch) <= 128):
            raise ValueError("Manual input must identify execution and control epoch")
        if type(sequence) is not int or sequence < 1:
            raise ValueError("Manual sequence must be a positive integer")
        if type(sent_ms) is not int or sent_ms <= 0:
            raise ValueError("Manual input timestamp must be positive milliseconds")
        return cls(execution_id, control_epoch, sequence, sent_ms, ManualAxes.from_wire(data))


@dataclass(frozen=True)
class ManualVelocity:
    forward_mps: float = 0.0
    right_mps: float = 0.0
    up_mps: float = 0.0
    yaw_rate_deg_s: float = 0.0


NEUTRAL_VELOCITY = ManualVelocity()


@dataclass(frozen=True)
class ManualSafetyDecision:
    accepted: bool
    velocity: ManualVelocity = NEUTRAL_VELOCITY
    code: str | None = None
    warning: str | None = None
    agl_m: float | None = None


@dataclass(frozen=True)
class ManualSafetyLimits:
    horizontal_mps: float = 2.0
    vertical_mps: float = 0.75
    yaw_deg_s: float = 20.0
    min_agl_m: float = 5.0
    max_agl_m: float = 100.0
    # Initial values for Webots tuning; the buffer is not a hard clearance guarantee.
    lower_buffer_m: float = 0.75
    upper_buffer_m: float = 1.0
    release_hysteresis_m: float = 0.5
    terrain_lookahead_s: float = 1.0
    input_max_age_ms: int = 500
    future_tolerance_ms: int = 250
    watchdog_s: float = 0.45
    telemetry_max_age_s: float = 1.5

    def __post_init__(self) -> None:
        scalars = (self.horizontal_mps, self.vertical_mps, self.yaw_deg_s,
                   self.min_agl_m, self.max_agl_m, self.lower_buffer_m,
                   self.upper_buffer_m, self.release_hysteresis_m,
                   self.terrain_lookahead_s, self.watchdog_s, self.telemetry_max_age_s)
        if not all(math.isfinite(value) for value in scalars):
            raise ValueError("Manual flight limits must be finite")
        if (min(self.horizontal_mps, self.vertical_mps, self.yaw_deg_s,
                self.watchdog_s, self.telemetry_max_age_s) <= 0
                or self.min_agl_m < 0 or self.max_agl_m <= self.min_agl_m
                or min(self.lower_buffer_m, self.upper_buffer_m,
                       self.release_hysteresis_m, self.terrain_lookahead_s) < 0
                or self.min_agl_m + self.lower_buffer_m + self.release_hysteresis_m
                   >= self.max_agl_m - self.upper_buffer_m - self.release_hysteresis_m
                or type(self.input_max_age_ms) is not int or self.input_max_age_ms <= 0
                or type(self.future_tolerance_ms) is not int or self.future_tolerance_ms < 0):
            raise ValueError("Manual flight limits are inconsistent")


class ManualInputSafety:
    """Validate one socket/epoch, normalize speed, and hold conservative AGL lockouts.

    Calls to ``evaluate`` and ``neutral_required`` must be serialized by the
    owning control service. Every rejected or unsafe input means a NEUTRAL
    velocity should be sent (while this session still owns the vehicle).
    A held key cannot restart movement after a watchdog/fault or AGL lockout:
    the operator must release all movement keys and press again.
    """

    def __init__(self, terrain: TerrainGrid, execution_id: str, control_epoch: str,
                 limits: ManualSafetyLimits | None = None) -> None:
        if not execution_id or not control_epoch:
            raise ValueError("Manual session needs execution and control epoch")
        self.terrain = terrain
        self.execution_id = execution_id
        self.control_epoch = control_epoch
        self.limits = limits or ManualSafetyLimits()
        self._last_sequence = 0
        self._last_input_at_s: float | None = None
        self._requires_neutral = True  # fresh ownership starts with released keys
        self._altitude_lock: str | None = None

    def _healthy(self, telemetry: TelemetrySample | None, status: VehicleStatus,
                 now_utc: datetime) -> bool:
        if not status.connected or not status.armed or status.flight_mode != "GUIDED":
            return False
        if telemetry is None or telemetry.timestamp_utc.tzinfo is None:
            return False
        age_s = (now_utc - telemetry.timestamp_utc).total_seconds()
        return (-0.25 <= age_s <= self.limits.telemetry_max_age_s
                and all(math.isfinite(value) for value in
                        (telemetry.latitude_deg, telemetry.longitude_deg,
                         telemetry.relative_altitude_m, telemetry.ground_speed_mps))
                and telemetry.ground_speed_mps >= 0
                and -90 <= telemetry.latitude_deg <= 90
                and -180 <= telemetry.longitude_deg <= 180)

    def neutral_required(self, *, now_monotonic_s: float,
                         now_utc: datetime, telemetry: TelemetrySample | None,
                         status: VehicleStatus) -> bool:
        """Called at watchdog cadence even if no frames arrive; send neutral when True.

        A bad/stale position or vehicle heartbeat always stops manual movement,
        independent of fresh browser packets. Recovery requires released keys.
        """
        healthy = self._healthy(telemetry, status, now_utc)
        if healthy:
            assert telemetry is not None
            try:
                healthy = math.isfinite(self.terrain.height_geo(
                    telemetry.latitude_deg, telemetry.longitude_deg))
            except (ValueError, OverflowError):
                healthy = False
        timed_out = (self._last_input_at_s is None or
                     now_monotonic_s - self._last_input_at_s > self.limits.watchdog_s or
                     now_monotonic_s < self._last_input_at_s)
        if not healthy or timed_out:
            self._requires_neutral = True
            return True
        return False

    def evaluate(self, frame: ManualInputFrame, telemetry: TelemetrySample | None,
                 status: VehicleStatus, *, now_utc: datetime | None = None,
                 now_monotonic_s: float) -> ManualSafetyDecision:
        now_utc = now_utc or datetime.now(timezone.utc)
        now_ms = now_utc.timestamp() * 1000
        if frame.execution_id != self.execution_id or frame.control_epoch != self.control_epoch:
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="WRONG_AUTHORITY", warning="Manual control session changed")
        if frame.sequence <= self._last_sequence:
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="INPUT_SEQUENCE", warning="Out-of-order manual input")
        age_ms = now_ms - frame.client_sent_at_ms
        if (age_ms > self.limits.input_max_age_ms or
                age_ms < -self.limits.future_tolerance_ms):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="INPUT_STALE", warning="Manual input is out of date")
        if not isinstance(frame.axes, ManualAxes) or any(
            type(value) is not int or value not in (-1, 0, 1) for value in
            (frame.axes.forward, frame.axes.right, frame.axes.up, frame.axes.yaw)
        ):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="INPUT_AXES", warning="Invalid manual direction")
        if not self._healthy(telemetry, status, now_utc):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="VEHICLE_UNAVAILABLE",
                                        warning="Vehicle telemetry unavailable — manual movement stopped")
        assert telemetry is not None
        self._last_sequence = frame.sequence
        self._last_input_at_s = now_monotonic_s

        try:
            ground = self.terrain.height_geo(telemetry.latitude_deg, telemetry.longitude_deg)
        except (ValueError, OverflowError):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="TERRAIN_UNAVAILABLE",
                                        warning="Terrain data unavailable — manual movement stopped")
        agl = telemetry.relative_altitude_m - ground
        if not math.isfinite(agl):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="TERRAIN_UNAVAILABLE",
                                        warning="Terrain data unavailable — manual movement stopped")

        # A disconnect, bad input, or watchdog timeout must not replay held keys.
        if self._requires_neutral:
            if not frame.axes.neutral:
                return ManualSafetyDecision(True, code="RELEASE_REQUIRED",
                                            warning="Release flight keys before moving", agl_m=agl)
            self._requires_neutral = False

        horizontal_speed = math.hypot(frame.axes.forward, frame.axes.right)
        forward = right = 0.0
        if horizontal_speed:
            factor = self.limits.horizontal_mps / horizontal_speed
            forward = frame.axes.forward * factor
            right = frame.axes.right * factor

        try:
            low_ground, high_ground = ground, ground
            if horizontal_speed:
                if (telemetry.heading_deg is None or
                        not math.isfinite(telemetry.heading_deg) or
                        not 0 <= telemetry.heading_deg < 360):
                    self._requires_neutral = True
                    return ManualSafetyDecision(False, code="HEADING_UNAVAILABLE",
                                                warning="Vehicle heading unavailable — manual movement stopped",
                                                agl_m=agl)
                # Webots x=north and y=west; BODY_OFFSET_NED uses +right=east.
                heading = math.radians(telemetry.heading_deg)
                north_mps = forward * math.cos(heading) - right * math.sin(heading)
                west_mps = -forward * math.sin(heading) - right * math.cos(heading)
                north, west = self.terrain.geo_to_local(telemetry.latitude_deg,
                                                         telemetry.longitude_deg)
                travel_m = self.limits.terrain_lookahead_s
                # Inspect the anticipated path, not only ground beneath the drone.
                for fraction in (0.25, 0.5, 0.75, 1.0):
                    height = self.terrain.height_local(
                        north + north_mps * travel_m * fraction,
                        west + west_mps * travel_m * fraction,
                    )
                    low_ground = min(low_ground, height)
                    high_ground = max(high_ground, height)
        except (ValueError, OverflowError):
            self._requires_neutral = True
            return ManualSafetyDecision(False, code="TERRAIN_UNAVAILABLE",
                                        warning="Terrain data unavailable — manual movement stopped",
                                        agl_m=agl)

        # Add a modest braking allowance proportional to observed ground speed;
        # actual deceleration/terrain tracking will be checked in Webots.
        braking_buffer = min(1.0, telemetry.ground_speed_mps * 0.35)
        lower_risk = telemetry.relative_altitude_m - high_ground <= (
            self.limits.min_agl_m + self.limits.lower_buffer_m + braking_buffer)
        upper_risk = telemetry.relative_altitude_m - low_ground >= (
            self.limits.max_agl_m - self.limits.upper_buffer_m - braking_buffer)
        lower_clear = agl >= (self.limits.min_agl_m + self.limits.lower_buffer_m
                              + braking_buffer + self.limits.release_hysteresis_m)
        upper_clear = agl <= (self.limits.max_agl_m - self.limits.upper_buffer_m
                              - braking_buffer - self.limits.release_hysteresis_m)

        # Existing lockouts remain active through key-repeat packets. A new
        # neutral packet after recovered clearance releases them; the next press
        # can then initiate ordinary movement. Prevents held W replaying on climb.
        if self._altitude_lock is not None:
            if self._altitude_lock == "LOW" and upper_risk:
                self._altitude_lock = "HIGH"
            elif self._altitude_lock == "HIGH" and lower_risk:
                self._altitude_lock = "LOW"
            elif frame.axes.neutral and lower_clear and upper_clear:
                self._altitude_lock = None
        if self._altitude_lock is None:
            if lower_risk:
                self._altitude_lock = "LOW"
            elif upper_risk:
                self._altitude_lock = "HIGH"

        if self._altitude_lock == "LOW":
            return ManualSafetyDecision(True,
                                        ManualVelocity(up_mps=self.limits.vertical_mps
                                                       if frame.axes.up == 1 else 0.0),
                                        code="TERRAIN_FLOOR",
                                        warning="Terrain clearance limit — climb to continue",
                                        agl_m=agl)
        if self._altitude_lock == "HIGH":
            return ManualSafetyDecision(True,
                                        ManualVelocity(up_mps=-self.limits.vertical_mps
                                                       if frame.axes.up == -1 else 0.0),
                                        code="TERRAIN_CEILING",
                                        warning="Terrain ceiling limit — descend to continue",
                                        agl_m=agl)
        return ManualSafetyDecision(True, ManualVelocity(
            forward_mps=forward, right_mps=right,
            up_mps=frame.axes.up * self.limits.vertical_mps,
            yaw_rate_deg_s=frame.axes.yaw * self.limits.yaw_deg_s,
        ), agl_m=agl)
