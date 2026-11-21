from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models.telemetry import TelemetrySample
from app.domain.models.vehicle_status import VehicleStatus
from app.geospatial.terrain_grid import TerrainGrid
from app.mission.manual_input_safety import (
    ManualAxes, ManualInputFrame, ManualInputSafety, ManualSafetyLimits,
    ManualVelocity, NEUTRAL_VELOCITY,
)


NOW = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)
ZERO = ManualAxes(0, 0, 0, 0)


def grid_with_heights(height_at_north=lambda north: 0.0) -> TerrainGrid:
    heights = tuple(tuple(float(height_at_north((i - 20) * 2)) for i in range(41))
                    for _ in range(41))
    grid = TerrainGrid("manual-test", 30.2672, -97.7431, 170, 80, 80, 2, heights)
    grid.validate()
    return grid


def telemetry(terrain: TerrainGrid, *, north: float = 0.0, west: float = 0.0,
              alt: float = 18, heading: float | None = 0.0,
              timestamp: datetime = NOW) -> TelemetrySample:
    lat, lon = terrain.local_to_geo(north, west)
    return TelemetrySample(1, timestamp, lat, lon, alt, heading, 0.0)


def vehicle_status(mode: str = "GUIDED", *, armed: bool = True,
                   connected: bool = True) -> VehicleStatus:
    return VehicleStatus(connected, armed, mode, NOW)


def frame(sequence: int, axes: ManualAxes = ZERO, *, ms: int = MS,
          execution_id: str = "mission-1", epoch: str = "epoch-1") -> ManualInputFrame:
    return ManualInputFrame(execution_id, epoch, sequence, ms, axes)


def make_gate(terrain: TerrainGrid | None = None) -> tuple[ManualInputSafety, TerrainGrid]:
    terrain = terrain or grid_with_heights()
    return ManualInputSafety(terrain, "mission-1", "epoch-1"), terrain


def evaluate(gate: ManualInputSafety, sample: TelemetrySample, sequence: int,
             axes: ManualAxes = ZERO, *, ms: int = MS,
             now: datetime = NOW, mono: float = 10.0,
             status: VehicleStatus | None = None):
    return gate.evaluate(frame(sequence, axes, ms=ms), sample,
                         status or vehicle_status(), now_utc=now, now_monotonic_s=mono)


def arm_input(gate: ManualInputSafety, sample: TelemetrySample) -> None:
    result = evaluate(gate, sample, 1)
    assert result.accepted and result.velocity == NEUTRAL_VELOCITY


@pytest.mark.parametrize("axes", [
    {"forward": True, "right": 0, "up": 0, "yaw": 0},
    {"forward": 2, "right": 0, "up": 0, "yaw": 0},
    {"forward": 0.5, "right": 0, "up": 0, "yaw": 0},
    {"forward": "1", "right": 0, "up": 0, "yaw": 0},
    {"forward": 0, "right": 0, "up": 0},
])
def test_wire_rejects_non_unit_axes(axes):
    with pytest.raises(ValueError, match="axes"):
        ManualAxes.from_wire(axes)


@pytest.mark.parametrize("replacement", [
    {"executionId": ""}, {"controlEpoch": ""}, {"sequence": 0},
    {"sequence": 1.5}, {"sequence": True}, {"clientSentAtMs": "123"},
    {"clientSentAtMs": 0},
])
def test_wire_rejects_bad_envelope(replacement):
    wire = dict(executionId="mission-1", controlEpoch="epoch-1", sequence=1,
                clientSentAtMs=MS, forward=0, right=0, up=0, yaw=0)
    wire.update(replacement)
    with pytest.raises(ValueError):
        ManualInputFrame.from_wire(wire)


def test_wire_parses_frontend_client_contract():
    wire = dict(executionId="mission-1", controlEpoch="epoch-1", sequence=7,
                clientSentAtMs=MS, forward=-1, right=1, up=1, yaw=-1)
    assert ManualInputFrame.from_wire(wire) == frame(
        7, ManualAxes(-1, 1, 1, -1))


def test_manual_speeds_and_simultaneous_axes():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 1, 1, -1))
    assert result.accepted and result.code is None
    assert result.velocity.forward_mps == pytest.approx(math.sqrt(2))
    assert result.velocity.right_mps == pytest.approx(math.sqrt(2))
    assert math.hypot(result.velocity.forward_mps,
                      result.velocity.right_mps) == pytest.approx(2)
    assert result.velocity.up_mps == pytest.approx(0.75)
    assert result.velocity.yaw_rate_deg_s == -20


@pytest.mark.parametrize(("axes", "expected"), [
    (ManualAxes(1, 0, 0, 0), ManualVelocity(2, 0, 0, 0)),
    (ManualAxes(-1, 0, 0, 0), ManualVelocity(-2, 0, 0, 0)),
    (ManualAxes(0, 1, 0, 0), ManualVelocity(0, 2, 0, 0)),
    (ManualAxes(0, -1, 0, 0), ManualVelocity(0, -2, 0, 0)),
    (ManualAxes(0, 0, -1, -1), ManualVelocity(0, 0, -0.75, -20)),
    (ZERO, NEUTRAL_VELOCITY),
])
def test_independent_directional_commands(axes, expected):
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 2, axes).velocity == expected


@pytest.mark.parametrize("bad", [
    {"execution_id": "mission-other"}, {"epoch": "epoch-old"},
    {"ms": MS - 501}, {"ms": MS + 251},
])
def test_rejects_wrong_epoch_and_stale_or_future_input(bad):
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    packet = frame(2, ManualAxes(1, 0, 0, 0), **bad)
    result = gate.evaluate(packet, sample, vehicle_status(),
                           now_utc=NOW, now_monotonic_s=10)
    assert not result.accepted
    assert result.velocity == NEUTRAL_VELOCITY
    # Previously-held movement is not restarted after bad packet.
    result = evaluate(gate, sample, 3, ManualAxes(1, 0, 0, 0))
    assert result.code == "RELEASE_REQUIRED"
    assert result.velocity == NEUTRAL_VELOCITY
    assert evaluate(gate, sample, 4).accepted
    assert evaluate(gate, sample, 5, ManualAxes(1, 0, 0, 0)).velocity.forward_mps == 2


def test_duplicated_and_out_of_order_sequence_cannot_drive_vehicle():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 5, ManualAxes(1, 0, 0, 0)).accepted
    for old in (5, 3):
        result = evaluate(gate, sample, old, ManualAxes(1, 0, 0, 0))
        assert not result.accepted and result.code == "INPUT_SEQUENCE"
    assert evaluate(gate, sample, 6, ManualAxes(1, 0, 0, 0)).velocity == NEUTRAL_VELOCITY


@pytest.mark.parametrize("status", [
    vehicle_status("BRAKE"), vehicle_status("RTL"),
    vehicle_status("GUIDED", armed=False),
    vehicle_status("GUIDED", connected=False),
])
def test_vehicle_mode_arming_and_heartbeat_fail_neutral(status):
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    result = evaluate(gate, sample, 1, ManualAxes(1, 0, 0, 0), status=status)
    assert not result.accepted and result.velocity == NEUTRAL_VELOCITY
    assert result.code == "VEHICLE_UNAVAILABLE"


@pytest.mark.parametrize("sample", [
    lambda terrain: None,
    lambda terrain: telemetry(terrain, timestamp=NOW - timedelta(seconds=2)),
    lambda terrain: telemetry(terrain, timestamp=NOW + timedelta(seconds=1)),
    lambda terrain: telemetry(terrain, alt=float('nan')),
])
def test_missing_stale_or_invalid_telemetry_fails_neutral(sample):
    gate, terrain = make_gate()
    result = gate.evaluate(frame(1, ManualAxes(1, 0, 0, 0)), sample(terrain),
                           vehicle_status(), now_utc=NOW, now_monotonic_s=10)
    assert not result.accepted and result.code == "VEHICLE_UNAVAILABLE"
    assert result.velocity == NEUTRAL_VELOCITY


def test_missing_heading_blocks_translation_but_not_neutral():
    gate, terrain = make_gate()
    sample = telemetry(terrain, heading=None)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0))
    assert not result.accepted and result.code == "HEADING_UNAVAILABLE"
    assert result.velocity == NEUTRAL_VELOCITY


def test_outside_terrain_grid_fails_neutral():
    gate, terrain = make_gate()
    sample = telemetry(terrain, north=41)
    result = evaluate(gate, sample, 1)
    assert not result.accepted and result.code == "TERRAIN_UNAVAILABLE"
    assert result.velocity == NEUTRAL_VELOCITY


def test_projected_path_outside_grid_fails_neutral():
    gate, terrain = make_gate()
    sample = telemetry(terrain, north=39, alt=18)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0))
    assert not result.accepted and result.code == "TERRAIN_UNAVAILABLE"
    assert result.velocity == NEUTRAL_VELOCITY


@pytest.mark.parametrize("alt", [4.0, 5.0, 5.5, 5.75])
def test_lower_agl_lock_climb_only(alt):
    gate, terrain = make_gate()
    sample = telemetry(terrain, alt=alt)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 1, 1, 1))
    assert result.accepted and result.code == "TERRAIN_FLOOR"
    assert result.velocity == ManualVelocity(up_mps=0.75)
    assert result.agl_m == pytest.approx(alt)
    assert evaluate(gate, sample, 3, ManualAxes(1, 0, -1, 0)).velocity == NEUTRAL_VELOCITY


@pytest.mark.parametrize("alt", [99.0, 99.5, 100.0, 101.0])
def test_upper_agl_lock_descend_only(alt):
    gate, terrain = make_gate()
    sample = telemetry(terrain, alt=alt)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 1, -1, 1))
    assert result.accepted and result.code == "TERRAIN_CEILING"
    assert result.velocity == ManualVelocity(up_mps=-0.75)
    assert evaluate(gate, sample, 3, ManualAxes(0, 0, 1, 1)).velocity == NEUTRAL_VELOCITY


def test_lower_lock_hysteresis_does_not_replay_held_w():
    gate, terrain = make_gate()
    start = telemetry(terrain, alt=5.5)
    arm_input(gate, start)
    assert evaluate(gate, start, 2, ManualAxes(1, 0, 1, 0)).code == "TERRAIN_FLOOR"
    safer = telemetry(terrain, alt=6.5)
    # The same held W remains suppressed even after the climb has enough clearance.
    held = evaluate(gate, safer, 3, ManualAxes(1, 0, 1, 0))
    assert held.code == "TERRAIN_FLOOR" and held.velocity == ManualVelocity(up_mps=0.75)
    assert evaluate(gate, safer, 4).velocity == NEUTRAL_VELOCITY
    assert evaluate(gate, safer, 5, ManualAxes(1, 0, 0, 0)).velocity.forward_mps == 2


def test_lower_lock_does_not_unlock_before_clearance():
    gate, terrain = make_gate()
    low = telemetry(terrain, alt=5.5)
    arm_input(gate, low)
    assert evaluate(gate, low, 2, ManualAxes(0, 0, 1, 0)).code == "TERRAIN_FLOOR"
    assert evaluate(gate, telemetry(terrain, alt=6.1), 3).code == "TERRAIN_FLOOR"
    assert evaluate(gate, telemetry(terrain, alt=6.5), 4).code is None


def test_ceiling_lock_requires_new_keypress_after_descent():
    gate, terrain = make_gate()
    high = telemetry(terrain, alt=99.5)
    arm_input(gate, high)
    assert evaluate(gate, high, 2, ManualAxes(0, 0, -1, 0)).code == "TERRAIN_CEILING"
    lower = telemetry(terrain, alt=97.0)
    assert evaluate(gate, lower, 3, ManualAxes(1, 0, -1, 0)).code == "TERRAIN_CEILING"
    assert evaluate(gate, lower, 4).code is None
    assert evaluate(gate, lower, 5, ManualAxes(1, 0, 0, 1)).velocity == ManualVelocity(2, 0, 0, 20)


def test_terrain_height_not_home_altitude_drives_lower_limit():
    terrain = grid_with_heights(lambda north: min(6, max(0, north * 0.5)))
    gate, _ = make_gate(terrain)
    sample = telemetry(terrain, north=12, alt=11.5)  # local terrain +6, AGL 5.5
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 0, 1, 1))
    assert result.agl_m == pytest.approx(5.5, abs=0.01)
    assert result.velocity == ManualVelocity(up_mps=0.75)


def test_lookahead_blocks_uphill_direction_before_terrain_closure():
    terrain = grid_with_heights(lambda north: min(3, max(0, (north - 3) * 2)))
    gate, _ = make_gate(terrain)
    sample = telemetry(terrain, north=3, alt=7.0, heading=0)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0))
    assert result.code == "TERRAIN_FLOOR"
    assert result.velocity == NEUTRAL_VELOCITY


def test_lookahead_uses_body_heading_for_right_vector():
    terrain = grid_with_heights(lambda north: min(3, max(0, (north - 3) * 2)))
    gate, _ = make_gate(terrain)
    sample = telemetry(terrain, north=3, alt=7.0, heading=270)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 2, ManualAxes(0, 1, 0, 0)).code == "TERRAIN_FLOOR"


def test_lookahead_blocks_descent_from_falling_terrain_ceiling():
    terrain = grid_with_heights(lambda north: -min(4, max(0, (north - 3) * 3)))
    gate, _ = make_gate(terrain)
    sample = telemetry(terrain, north=3, alt=97, heading=0)
    arm_input(gate, sample)
    result = evaluate(gate, sample, 2, ManualAxes(1, 0, -1, 0))
    assert result.code == "TERRAIN_CEILING"
    assert result.velocity == ManualVelocity(up_mps=-0.75)


def test_watchdog_is_independent_of_input_socket_and_requires_release():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0)).velocity.forward_mps == 2
    assert not gate.neutral_required(now_monotonic_s=10.3, now_utc=NOW,
                                     telemetry=sample, status=vehicle_status())
    assert gate.neutral_required(now_monotonic_s=10.46, now_utc=NOW,
                                 telemetry=sample, status=vehicle_status())
    assert evaluate(gate, sample, 3, ManualAxes(1, 0, 0, 0), mono=10.5).code == "RELEASE_REQUIRED"
    assert evaluate(gate, sample, 4, mono=10.6).accepted
    assert evaluate(gate, sample, 5, ManualAxes(1, 0, 0, 0), mono=10.7).velocity.forward_mps == 2


def test_watchdog_neutralizes_fresh_input_with_lost_telemetry():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0)).accepted
    assert gate.neutral_required(now_monotonic_s=10.1,
        now_utc=NOW + timedelta(seconds=2), telemetry=sample, status=vehicle_status())
    assert evaluate(gate, sample, 3, ManualAxes(1, 0, 0, 0)).code == "RELEASE_REQUIRED"


def test_watchdog_neutralizes_disarmed_and_rtl_aircraft():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    for status in (vehicle_status("RTL"), vehicle_status(armed=False), vehicle_status(connected=False)):
        assert gate.neutral_required(now_monotonic_s=10.1, now_utc=NOW,
                                     telemetry=sample, status=status)


def test_invalid_limits_are_rejected():
    with pytest.raises(ValueError, match="inconsistent"):
        ManualSafetyLimits(min_agl_m=99)
    with pytest.raises(ValueError, match="inconsistent"):
        ManualSafetyLimits(horizontal_mps=0)
    with pytest.raises(ValueError, match="finite"):
        ManualSafetyLimits(terrain_lookahead_s=float('nan'))


def test_watchdog_neutralizes_unknown_terrain_even_with_fresh_input():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    arm_input(gate, sample)
    assert evaluate(gate, sample, 2, ManualAxes(1, 0, 0, 0)).accepted
    outside = telemetry(terrain, north=41)
    assert gate.neutral_required(now_monotonic_s=10.1, now_utc=NOW,
                                 telemetry=outside, status=vehicle_status())
    assert evaluate(gate, sample, 3, ManualAxes(1, 0, 0, 0)).code == "RELEASE_REQUIRED"


def test_nonfinite_ground_speed_rejects_telemetry():
    gate, terrain = make_gate()
    sample = telemetry(terrain)
    corrupted = TelemetrySample(sample.sequence, NOW, sample.latitude_deg,
                                sample.longitude_deg, sample.relative_altitude_m,
                                sample.heading_deg, float("nan"))
    result = evaluate(gate, corrupted, 1)
    assert not result.accepted and result.code == "VEHICLE_UNAVAILABLE"


def test_ground_speed_expands_predictive_braking_buffer():
    gate, terrain = make_gate()
    sample = telemetry(terrain, alt=6.1)
    moving = TelemetrySample(sample.sequence, NOW, sample.latitude_deg,
                             sample.longitude_deg, sample.relative_altitude_m,
                             sample.heading_deg, 2.0)
    arm_input(gate, moving)
    assert evaluate(gate, moving, 2, ManualAxes(1, 0, 0, 0)).code == "TERRAIN_FLOOR"
    assert evaluate(gate, moving, 3, ManualAxes(0, 0, 1, 0)).velocity == ManualVelocity(up_mps=0.75)
