#!/usr/bin/env python3
"""Check/apply project-owned camera patches to the user's ArduPilot Webots tree.

Requires existing ArduPilot 4.7 Webots files; original world and C++ SITL patch
remain untouched. Source controllers are never redistributed in the SAR repo.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
UPSTREAM = Path('libraries/SITL/examples/Webots_Python')
ORIGINAL_WORLD = UPSTREAM / 'worlds/iris_wsl.wbt'
NEW_WORLD = UPSTREAM / 'worlds/iris_wsl_camera.wbt'
CONTROLLERS = (
    'controllers/ardupilot_vehicle_controller/webots_vehicle.py',
    'controllers/ardupilot_vehicle_controller/ardupilot_vehicle_controller.py',
)
SENDER = UPSTREAM / 'controllers/ardupilot_vehicle_controller/sar_camera_publisher.py'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(ardupilot: Path):
    sys.path.insert(0, str(ROOT))
    from world_camera_overlay import make_camera_world, normalize_sitl_address, CAMERA_EXTENSION

    metadata = json.loads((ROOT / 'expected_upstream_sha256.json').read_text())
    base = ardupilot / ORIGINAL_WORLD
    if not base.is_file():
        raise ValueError(f'Missing operating world: {base}')
    original = base.read_text(encoding='utf-8')
    normalized_hash = digest(normalize_sitl_address(original).encode())
    if normalized_hash != metadata['worlds/iris_wsl.wbt']['normalized']:
        raise ValueError('Existing iris_wsl.wbt differs from uploaded source, beyond SITL address. Refusing to generate new world.')
    camera_world = make_camera_world(original).encode()
    # The previously installed camera world used a 0.70-rad mount. Permit
    # exactly that known generated file to be upgraded; never overwrite a
    # user-edited or foreign world on a guess.
    legacy_camera_world = make_camera_world(original, extension=CAMERA_EXTENSION).encode()

    updated = {}
    # Run all patches in an isolated temporary directory before touching live files.
    with tempfile.TemporaryDirectory(prefix='sar-camera-patch-') as td:
        temp = Path(td)
        for rel in CONTROLLERS:
            dest = ardupilot / UPSTREAM / rel
            if not dest.is_file():
                raise ValueError(f'Missing controller source: {dest}')
            data = dest.read_bytes()
            versions = metadata[rel]
            if digest(data) == versions['patched']:
                updated[UPSTREAM / rel] = data
            elif digest(data) == versions['original']:
                staging = temp / UPSTREAM / rel
                staging.parent.mkdir(parents=True, exist_ok=True)
                staging.write_bytes(data)
                patch = ROOT / 'patches' / versions['patch_file']
                result = subprocess.run(['git', 'apply', '--check', str(patch)], cwd=temp, capture_output=True, text=True)
                if result.returncode:
                    raise ValueError(f'Patch check failed: {rel}\n{result.stderr}')
                result = subprocess.run(['git', 'apply', str(patch)], cwd=temp, capture_output=True, text=True)
                if result.returncode:
                    raise ValueError(f'Patch failed: {rel}\n{result.stderr}')
                updated[UPSTREAM / rel] = staging.read_bytes()
            else:
                raise ValueError(f'Existing {rel} has unexpected edits; compare before installing.')
            if digest(updated[UPSTREAM / rel]) != versions['patched']:
                raise ValueError(f'Patched controller hash mismatch: {rel}')

    updated[NEW_WORLD] = camera_world
    updated[SENDER] = (ROOT / 'sar_camera_publisher.py').read_bytes()
    statuses = []
    for rel, content in updated.items():
        dest = ardupilot / rel
        if dest.exists():
            status = 'already installed' if dest.read_bytes() == content else None
            if status is None and rel == NEW_WORLD and dest.read_bytes() == legacy_camera_world:
                status = 'safe to patch (known version)'
            if status is None and rel not in tuple(UPSTREAM / x for x in CONTROLLERS):
                raise ValueError(f'Unexpected existing file: {dest}. Not overwriting.')
            if status is None:
                status = 'safe to patch (known version)'
        else:
            status = 'new file'
        statuses.append((rel, content, status))
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ardupilot', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    ardupilot = args.ardupilot.expanduser().resolve()
    statuses = prepare(ardupilot)
    for rel, _, status in statuses:
        print(f'{status:35} {rel}')
    if args.check:
        print('PASS: no files modified.')
        return 0

    backup = ardupilot / 'sar-camera-source-backup' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for rel, data, status in statuses:
        if status == 'safe to patch (known version)':
            target = backup / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ardupilot / rel, target)
    if backup.exists():
        print('Original controller backups:', backup)
    for rel, data, status in statuses:
        if status == 'already installed':
            continue
        dest = ardupilot / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print('Wrote', dest)
    print('Open iris_wsl_camera.wbt in Webots, not the unchanged iris_wsl.wbt.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as e:
        print('BLOCKED:', e, file=sys.stderr)
        sys.exit(2)
