import importlib.util
import json
import math
from pathlib import Path

import pytest
from app.vision.camera_geometry import load_camera_geometry, CameraGeometry

ROOT = Path(__file__).resolve().parents[2]


def test_shared_json_geometry_matches_generated_world():
    config = load_camera_geometry()
    assert config.pitch_down_deg == pytest.approx(60)
    assert config.source_id == "webots-iris"
    spec = importlib.util.spec_from_file_location(
        "world_camera_overlay_geometry", ROOT / "simulator/webots/world_camera_overlay.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = module.load_world_camera_geometry()
    assert values["mountForwardM"] == config.mount_forward_m
    assert values["pitchDownDeg"] == config.pitch_down_deg
    assert "rotation 0 1 0 1.04719755" in module.camera_extension()
    assert "fieldOfView 1.10000000" in module.camera_extension()
    assert "translation 0.32 0 0.05" in module.camera_extension()


def test_missing_or_changed_calibration_fails_closed(tmp_path):
    path = tmp_path / "camera_geometry.json"
    value = json.loads((ROOT / "config/camera_geometry.json").read_text())
    value["pitchDownDeg"] = -20
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_camera_geometry(path)
    value["pitchDownDeg"] = 60
    value["unexpectedField"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_camera_geometry(path)
