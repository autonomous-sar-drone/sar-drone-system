"""Validate generated scenario consistency before loading in Webots (read-only)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def verify(directory: Path) -> dict:
    manifest = json.loads((directory / "scenario_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "NOT_FLIGHT_VALIDATED":
        raise ValueError("Unexpected manifest status")
    world = directory / manifest["world_file"]
    data = directory / manifest["terrain_file"]
    for path, key in ((world, "world_sha256"), (data, "terrain_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[key]:
            raise ValueError(f"Manifest hash mismatch: {path.name}")
    text = world.read_text(encoding="utf-8")
    terrain = json.loads(data.read_text(encoding="utf-8"))
    if terrain["format"] != "sar-terrain-grid-v1" or 'gpsCoordinateSystem "local"' not in text:
        raise ValueError("Wrong terrain format or Webots GPS coordinate system")
    match = re.search(r'height \[(.*?)\]', text, re.S)
    if match is None:
        raise ValueError("World lacks an ElevationGrid height list")
    webots_heights = [float(n) for n in match.group(1).split()]
    backend_heights = [float(h) for row in terrain["heights_home_relative_m"] for h in row]
    if webots_heights != backend_heights:
        raise ValueError("Webots terrain heights and backend terrain.json differ")
    if len(webots_heights) != terrain["x_dimension"] * terrain["y_dimension"]:
        raise ValueError("Terrain grid dimensions disagree")
    if not re.search(r'"--sitl-address"\s+"[\d.]+"', text):
        raise ValueError("No explicit SITL address in Webots world")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_dir", type=Path)
    args = parser.parse_args()
    try:
        m = verify(args.scenario_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"INVALID SCENARIO: {exc}", file=sys.stderr)
        return 1
    print(f"CONSISTENT SCENARIO: {m['scenario']}")
    print(f"Height span: {m['height_span_m']} m")
    print(f"SITL --custom-location={m['sitl_custom_location']}")
    print(f"Actual controller SITL address: {m['webots_controller_sitl_address']}")
    print(f"Ground truth pedestrian: {m['pedestrian_ground_truth']}")
    print("Not yet Webots/ArduPilot flight-validated. No files were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
