from types import SimpleNamespace

import pytest
from app.vehicle.pose_history import PoseHistory


def position(boot=10000, alt=25000):
    return SimpleNamespace(time_boot_ms=boot, lat=302672000, lon=-977431000, relative_alt=alt)


def attitude(boot=10020, roll=0, pitch=0, yaw=0):
    return SimpleNamespace(time_boot_ms=boot, roll=roll, pitch=pitch, yaw=yaw)


def test_pose_history_matches_capture_time_and_stays_internal():
    hist = PoseHistory()
    hist.add_position(position(), at_unix_ms=100000)
    hist.add_attitude(attitude(), at_unix_ms=100020)
    pose = hist.at_capture(100010)
    assert pose.latitude_deg == pytest.approx(30.2672)
    assert pose.longitude_deg == pytest.approx(-97.7431)
    assert pose.relative_altitude_m == 25
    assert pose.pitch_rad == 0


def test_missing_stale_or_inconsistent_pose_never_produces_position():
    hist = PoseHistory()
    hist.add_position(position(), at_unix_ms=100000)
    assert hist.at_capture(100010) is None
    hist.add_attitude(attitude(boot=12000), at_unix_ms=100020)
    assert hist.at_capture(100010) is None
    hist.add_attitude(attitude(), at_unix_ms=100020)
    assert hist.at_capture(101000) is None
    assert hist.at_capture(100010) is not None


def test_no_pose_for_invalid_relative_altitude():
    hist = PoseHistory()
    hist.add_position(position(alt=0), at_unix_ms=100000)
    hist.add_attitude(attitude(), at_unix_ms=100010)
    assert hist.at_capture(100005) is None
