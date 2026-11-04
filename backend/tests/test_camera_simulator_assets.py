import importlib.util
from pathlib import Path
import re
import math
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[2]
SIMULATOR = ROOT / 'simulator' / 'webots'
CONTROLLER = SIMULATOR


def test_world_uses_existing_flight_settings_and_attaches_downward_camera():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location('world_overlay_test', SIMULATOR / 'world_camera_overlay.py')
    assert spec and spec.loader
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    # Real world is read from the user's ArduPilot installation by installer.
    minimal_world = 'WorldInfo { basicTimeStep 2 }\nIris {\n  controllerArgs [\n    "--motors"\n    "m1_motor, m2_motor, m3_motor, m4_motor"\n    "--sitl-address"\n    "172.21.1.2"\n  ]\n}\n'
    world = overlay.make_camera_world(minimal_world)
    assert '172.21.1.2' in world
    assert 'basicTimeStep 2' in world
    assert '--motors' in world
    assert '--sitl-address' in world
    assert '--sar-camera-host' in world
    assert '--sar-camera-port' in world
    assert 'name "sar_camera"' in world
    # Iris bounding box is 0.47 m in X: nose edge +0.235 m.
    # The sensor must not sit inside that body volume.
    assert 'translation 0.32 0 0.05' in world
    assert 'translation 0.05 0 0.05' not in world
    assert 'width 640' in world and 'height 360' in world
    match = re.search(r'rotation 0 1 0 ([0-9.]+)\s+width 640', world)
    assert match is not None
    # Webots Camera points along its local +X; positive Y rotation aims downward.
    assert float(match.group(1)) == pytest.approx(math.pi / 3, abs=1e-7)
    assert 'translation 0.32 0 0.05' in world


def test_webots_patch_reads_frames_after_robot_step_only():
    patch = (SIMULATOR / 'patches' / 'webots_vehicle.patch').read_text()
    assert 'self._offer_sar_frame()' in patch
    assert 'self.camera.getImage()' in patch
    assert 'self._sar_publisher.offer(' in patch
    # The flight-control thread remains responsible for the Webots API calls.
    sender = (SIMULATOR / 'sar_camera_publisher.py').read_text()
    assert '\n        robot.step(' not in sender
    assert '.getImage(' not in sender



def test_installer_blocks_unknown_local_changes(tmp_path):
    original = tmp_path / 'ardupilot' / 'libraries/SITL/examples/Webots_Python'
    source_dirs = [
        original / 'worlds',
        original / 'controllers' / 'ardupilot_vehicle_controller',
    ]
    for d in source_dirs:
        d.mkdir(parents=True)
    (source_dirs[1] / 'webots_vehicle.py').write_text('my uncommitted fix\n')
    (source_dirs[0] / 'iris_wsl.wbt').write_text('bad test world')
    # companion controller doesn't exist yet; that's fine, but unknown file is blocked.
    script = SIMULATOR / 'install.py'
    result = subprocess.run([sys.executable, str(script), '--ardupilot', str(tmp_path / 'ardupilot'), '--check'], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'BLOCKED' in result.stderr
    assert (source_dirs[1] / 'webots_vehicle.py').read_text() == 'my uncommitted fix\n'
