"""Offline, developer-only 3DEP -> Webots + backend shared terrain scenario builder.

Builds artifacts; never starts/stops Webots or ArduPilot, nor changes a live world.
The supplied source DEM must be real USGS or a plainly labeled test fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import ipaddress
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from pyproj import CRS, Transformer
import rasterio

USGS_EXPORT = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
EARTH_RADIUS_M = 6378137.0


class TerrainBuildError(ValueError):
    pass


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"scenario_id", "latitude_deg", "longitude_deg", "width_m", "height_m", "grid_spacing_m", "tree_count", "pedestrian_north_m", "pedestrian_west_m", "random_seed"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise TerrainBuildError(f"Scenario config needs keys: {sorted(required)}")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,60}", str(data["scenario_id"])):
        raise TerrainBuildError("scenario_id must be a simple filesystem name")
    lat, lon = float(data["latitude_deg"]), float(data["longitude_deg"])
    width, height, spacing = map(float, (data["width_m"], data["height_m"], data["grid_spacing_m"]))
    if not (20 < lat < 40 and -110 < lon < -90):
        raise TerrainBuildError("First prototype supports Texas geographic origins only")
    if not (60 <= width <= 800 and 60 <= height <= 800 and 1 <= spacing <= 10):
        raise TerrainBuildError("World must be 60..800 m wide/tall, spacing 1..10 m")
    if abs(width/spacing - round(width/spacing)) > 1e-8 or abs(height/spacing - round(height/spacing)) > 1e-8:
        raise TerrainBuildError("width_m and height_m must be multiples of grid_spacing_m")
    count = int(data["tree_count"])
    if not 0 <= count <= 100:
        raise TerrainBuildError("tree_count must be 0..100")
    return data


def _aeqd_transformers(lat: float, lon: float):
    aeqd = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")
    return (Transformer.from_crs(aeqd, "EPSG:4326", always_xy=True),
            Transformer.from_crs("EPSG:4326", aeqd, always_xy=True))


def _local_to_geo(north: np.ndarray, west: np.ndarray, lat: float, lon: float):
    to_geo, _ = _aeqd_transformers(lat, lon)
    return to_geo.transform(-west, north)


def fetch_usgs_dem(config: dict, destination: Path, *, force: bool = False, source_spacing_m: float = 2.0) -> Path:
    """One USGS 3DEP dynamic export per scenario (not one elevation request per point)."""
    if destination.is_file() and not force:
        return destination
    lat = float(config["latitude_deg"])
    lon = float(config["longitude_deg"])
    north_half = float(config["width_m"]) / 2 + 25
    west_half = float(config["height_m"]) / 2 + 25
    ns = np.asarray([-north_half, -north_half, north_half, north_half])
    ws = np.asarray([-west_half, west_half, -west_half, west_half])
    lons, lats = _local_to_geo(ns, ws, lat, lon)
    bbox = (float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max()))
    pixel_w = max(32, min(1000, int(math.ceil((2 * west_half) / source_spacing_m))))
    pixel_h = max(32, min(1000, int(math.ceil((2 * north_half) / source_spacing_m))))
    params = {
        "bbox": ",".join(f"{n:.9f}" for n in bbox), "bboxSR": "4326", "imageSR": "4326",
        "size": f"{pixel_w},{pixel_h}", "format": "tiff", "pixelType": "F32",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_BilinearInterpolation", "f": "image",
    }
    url = f"{USGS_EXPORT}?{urlencode(params)}"
    error = None
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "SAR-Capstone-Webots-Terrain/0.1 (academic scenario build)"})
            with urlopen(request, timeout=90) as response:
                payload = response.read(12_000_000)
            if len(payload) < 100 or payload[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
                raise TerrainBuildError(f"USGS returned unexpected content: {payload[:160]!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_suffix(".download")
            temp.write_bytes(payload)
            with rasterio.open(temp) as ds:
                if ds.count != 1 or ds.crs is None or ds.width < 2 or ds.height < 2:
                    raise TerrainBuildError("Export does not contain a valid georeferenced elevation band")
            temp.replace(destination)
            return destination
        except Exception as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise TerrainBuildError(f"Cannot retrieve USGS DEM (nothing was changed in Webots): {error}")


def _bilinear_source(dataset: rasterio.io.DatasetReader, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    if dataset.crs is None:
        raise TerrainBuildError("Input GeoTIFF has no CRS")
    to_source = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
    sx, sy = to_source.transform(lon, lat)
    inv = ~dataset.transform
    col, row = inv * (sx, sy)
    # Affine refers to pixel corner; interpolate between pixel centers.
    col = np.asarray(col) - 0.5
    row = np.asarray(row) - 0.5
    c0 = np.floor(col).astype(int)
    r0 = np.floor(row).astype(int)
    if np.any(c0 < 0) or np.any(r0 < 0) or np.any(c0 + 1 >= dataset.width) or np.any(r0 + 1 >= dataset.height):
        raise TerrainBuildError("DEM does not cover the world and interpolated launch center; request a larger GeoTIFF")
    data = dataset.read(1, masked=True)
    four = (data[r0, c0], data[r0, c0 + 1], data[r0 + 1, c0], data[r0 + 1, c0 + 1])
    if any(np.any(np.ma.getmaskarray(v)) for v in four):
        raise TerrainBuildError("DEM has missing elevation samples within the requested world")
    if any(np.any(~np.isfinite(v)) for v in four):
        raise TerrainBuildError("DEM contains non-finite elevations")
    u, v = col - c0, row - r0
    return ((1 - u) * (1 - v) * four[0] + u * (1 - v) * four[1] +
            (1 - u) * v * four[2] + u * v * four[3])


def raster_to_heights(config: dict, source_tiff: Path) -> tuple[np.ndarray, float, dict]:
    lat, lon = float(config["latitude_deg"]), float(config["longitude_deg"])
    spacing = float(config["grid_spacing_m"])
    x_n = int(round(float(config["width_m"]) / spacing)) + 1
    y_n = int(round(float(config["height_m"]) / spacing)) + 1
    north_axis = np.linspace(-float(config["width_m"])/2, float(config["width_m"])/2, x_n)
    west_axis = np.linspace(-float(config["height_m"])/2, float(config["height_m"])/2, y_n)
    north, west = np.meshgrid(north_axis, west_axis, indexing="xy")
    lons, lats = _local_to_geo(north, west, lat, lon)
    with rasterio.open(source_tiff) as ds:
        absolute = _bilinear_source(ds, np.asarray(lons), np.asarray(lats))
        origin = _bilinear_source(ds, np.asarray([lon]), np.asarray([lat]))[0]
        meta = {"source_crs": str(ds.crs), "source_resolution_units": [abs(ds.transform.a), abs(ds.transform.e)],
                "source_width_px": ds.width, "source_height_px": ds.height}
    heights = np.asarray(absolute - origin, dtype=float)
    # Store the exact same quantized height array in both Webots and terrain.json.
    heights = np.round(heights, 3)
    if not np.all(np.isfinite(heights)):
        raise TerrainBuildError("World contains non-finite heights")
    if not np.all(np.isfinite([origin])):
        raise TerrainBuildError("Cannot determine the home ground elevation")
    if heights.min() < -100 or heights.max() > 100:
        raise TerrainBuildError("Terrain relief >100 m; choose a gently rolling test region")
    return heights, float(origin), meta


def height_local(heights: np.ndarray, config: dict, north_m: float, west_m: float) -> float:
    spacing = float(config["grid_spacing_m"])
    i = (north_m + float(config["width_m"])/2) / spacing
    j = (west_m + float(config["height_m"])/2) / spacing
    if not (0 <= i <= heights.shape[1]-1 and 0 <= j <= heights.shape[0]-1):
        raise TerrainBuildError("Point outside generated world")
    i0, j0 = min(int(i), heights.shape[1]-2), min(int(j), heights.shape[0]-2)
    u, v = i - i0, j - j0
    return float((1-u)*(1-v)*heights[j0,i0] + u*(1-v)*heights[j0,i0+1] +
                 (1-u)*v*heights[j0+1,i0] + u*v*heights[j0+1,i0+1])


def _terrain_solid(config: dict, heights: np.ndarray) -> str:
    width, height, spacing = (float(config[k]) for k in ("width_m", "height_m", "grid_spacing_m"))
    lines = []
    for row in heights:
        lines.append("      " + " ".join(f"{number:.3f}" for number in row))
    return f'''# One height grid provides BOTH visual geometry and collision surface.
# World local +X = north; +Y = west; +Z = up. Origin is the drone's launch ground.
Solid {{
  translation {-width/2:.3f} {-height/2:.3f} 0
  name "3DEP terrain (home z=0)"
  children [
    Shape {{
      appearance PBRAppearance {{ baseColor 0.35 0.46 0.26 roughness 1 }}
      geometry DEF SAR_ELEVATION_GRID ElevationGrid {{
        xDimension {heights.shape[1]}
        xSpacing {spacing:.3f}
        yDimension {heights.shape[0]}
        ySpacing {spacing:.3f}
        thickness 1
        height [
{chr(10).join(lines)}
        ]
      }}
    }}
  ]
  boundingObject USE SAR_ELEVATION_GRID
  locked TRUE
}}
'''


def _trees(config: dict, heights: np.ndarray) -> str:
    rng = random.Random(int(config["random_seed"]))
    parts = []
    for index in range(int(config["tree_count"])):
        # Keep launch area and a central strip clear for initial navigation tests.
        for _ in range(60):
            north = rng.uniform(-float(config["width_m"])*.43, float(config["width_m"])*.43)
            west = rng.uniform(-float(config["height_m"])*.43, float(config["height_m"])*.43)
            if math.hypot(north, west) >= 24 and abs(west) >= 8 and math.hypot(north-float(config["pedestrian_north_m"]), west-float(config["pedestrian_west_m"])) > 8:
                break
        else:
            continue
        z = height_local(heights, config, north, west)
        trunk_h = rng.uniform(3.1, 5.0)
        crown_h = rng.uniform(2.8, 4.8)
        crown_r = rng.uniform(1.5, 2.7)
        parts.append(f'''Solid {{
  translation {north:.3f} {west:.3f} {z:.3f}
  name "low-poly tree {index+1}"
  children [
    Transform {{ translation 0 0 {trunk_h/2:.3f} children [
      Shape {{ appearance PBRAppearance {{ baseColor 0.36 0.23 0.12 roughness 1 }}
              geometry Cylinder {{ radius 0.19 height {trunk_h:.3f} subdivision 7 }} }}
    ] }}
    Transform {{ translation 0 0 {trunk_h + crown_h/2:.3f} children [
      Shape {{ appearance PBRAppearance {{ baseColor 0.14 0.31 0.13 roughness 1 }}
              geometry Cone {{ bottomRadius {crown_r:.3f} height {crown_h:.3f} subdivision 7 }} }}
    ] }}
  ]
  # Simple trunk collision only: canopy is visual occlusion, not a flight obstacle model.
  boundingObject Pose {{ translation 0 0 {trunk_h/2:.3f} children [
    Cylinder {{ radius 0.19 height {trunk_h:.3f} }}
  ] }}
  locked TRUE
}}
''')
    return "\n".join(parts)


def _iris_from_template(template: Path, sitl_ip: str | None = None) -> str:
    text = template.read_text(encoding="utf-8")
    marker = re.search(r"(?m)^Iris\s*\{", text)
    if marker is None:
        raise TerrainBuildError("Template lacks an Iris node")
    iris = text[marker.start():].strip()
    iris = re.sub(r"(?m)^\s*hidden (?:linearVelocity_0|angularVelocity_0) .*\n", "\n", iris)
    iris, count = re.subn(r"(?m)^(\s*translation)\s+[-\d.eE]+\s+[-\d.eE]+\s+[-\d.eE]+", r"\g<1> 0 0 0.055", iris, count=1)
    if count != 1:
        raise TerrainBuildError("Could not relocate Iris to geographic origin")
    if 'controller "ardupilot_vehicle_controller"' not in iris or '"--sitl-address"' not in iris or '"sar_camera"' not in iris:
        raise TerrainBuildError("Iris template is not the verified camera/SITL world")
    if sitl_ip is not None:
        ipaddress.ip_address(sitl_ip)  # reject malformed controller networking value
        iris, count = re.subn(
            r'("--sitl-address"\s+")[^"]+(")',
            lambda m: m.group(1) + sitl_ip + m.group(2), iris, count=1,
        )
        if count != 1:
            raise TerrainBuildError("Cannot update SITL address in Iris controllerArgs")
    return iris


def write_world(config: dict, heights: np.ndarray, template: Path, target: Path,
                sitl_ip: str | None = None) -> None:
    # IMPORTANT: Webots GPS MUST stay local, since WebotsArduVehicle forwards XYZ directly.
    # SITL applies its own geographic origin, separate from Webots GPS sensor output.
    ped_n, ped_w = float(config["pedestrian_north_m"]), float(config["pedestrian_west_m"])
    # Pedestrian PROTO origin is 1.27 m above its feet
    ped_z = height_local(heights, config, ped_n, ped_w) + 1.27
    source = f'''#VRML_SIM R2025a utf8
# OFFLINE GENERATED SCENARIO {config["scenario_id"]}; DO NOT REPLACE THE STABLE DEMO WORLD.
# The geo origin and exact terrain heights are in the adjacent terrain.json.
EXTERNPROTO "https://raw.githubusercontent.com/cyberbotics/webots/R2023a/projects/objects/backgrounds/protos/TexturedBackground.proto"
EXTERNPROTO "https://raw.githubusercontent.com/cyberbotics/webots/R2023a/projects/objects/backgrounds/protos/TexturedBackgroundLight.proto"
EXTERNPROTO "../protos/Iris.proto"
EXTERNPROTO "https://raw.githubusercontent.com/cyberbotics/webots/R2025a/projects/humans/pedestrian/protos/Pedestrian.proto"
WorldInfo {{
  title "SAR terrain: {config['scenario_id']}"
  basicTimeStep 2
  FPS 20
  gpsCoordinateSystem "local"
}}
Viewpoint {{
  orientation 0.14171802029368655 -0.19502169386003032 -0.9705063326161261 4.997768707865118
  position 24 -24 18
  follow "Iris"
  followSmoothness 0.01
}}
TexturedBackground {{}}
TexturedBackgroundLight {{}}
{_terrain_solid(config, heights)}
# A pedestrian at a known ground contact coordinate, for detector/location tests.
Pedestrian {{ translation {ped_n:.3f} {ped_w:.3f} {ped_z:.3f} }}
{_trees(config, heights)}
{_iris_from_template(template, sitl_ip)}
'''
    target.write_text(source, encoding="utf-8")


def build_scenario(config_path: Path, template: Path, output_root: Path, *, dem_path: Path | None, fetch: bool,
                   source_spacing_m: float = 2.0, force: bool = False,
                   sitl_ip: str | None = None) -> Path:
    config = load_config(config_path)
    output = output_root / config["scenario_id"]
    source = dem_path or (output / "usgs_source_dem.tif")
    if fetch:
        source = fetch_usgs_dem(config, source, force=force, source_spacing_m=source_spacing_m)
    elif not source.is_file():
        raise TerrainBuildError("No input DEM. Use --fetch-usgs or --dem path/to/real_elevation.tif")
    heights, origin_absolute, source_meta = raster_to_heights(config, source)
    output.mkdir(parents=True, exist_ok=True)
    # Manifest is written LAST; an interrupted regeneration must not leave
    # a stale 'valid' manifest referring to mismatched terrain/world files.
    (output / "scenario_manifest.json").unlink(missing_ok=True)
    name = config["scenario_id"]
    world_path = output / f"{name}.wbt"
    write_world(config, heights, template, world_path, sitl_ip=sitl_ip)
    terrain = {
        "format": "sar-terrain-grid-v1", "scenario_id": name,
        "origin_latitude_deg": float(config["latitude_deg"]),
        "origin_longitude_deg": float(config["longitude_deg"]),
        "origin_ground_elevation_dem_m": round(origin_absolute, 3),
        "world_x_is_north": True, "world_y_is_west": True,
        "width_m": float(config["width_m"]), "height_m": float(config["height_m"]),
        "grid_spacing_m": float(config["grid_spacing_m"]),
        "x_dimension": heights.shape[1], "y_dimension": heights.shape[0],
        "heights_home_relative_m": heights.tolist(),
        "source": "USGS 3DEP ImageServer" if fetch or "usgs" in source.name.lower() else "USER_SUPPLIED_DEM_VERIFY_PROVENANCE",
        "source_crs": source_meta["source_crs"],
    }
    terrain_path = output / "terrain.json"
    terrain_path.write_text(json.dumps(terrain, indent=1) + "\n", encoding="utf-8")
    pedestrian_lon, pedestrian_lat = _local_to_geo(
        np.asarray(float(config["pedestrian_north_m"])),
        np.asarray(float(config["pedestrian_west_m"])),
        float(config["latitude_deg"]), float(config["longitude_deg"]),
    )
    manifest = {
        "scenario": name,
        "status": "NOT_FLIGHT_VALIDATED",
        "dem_source": str(source),
        "webots_controller_sitl_address": sitl_ip if sitl_ip is not None else "UNCHANGED_FROM_TEMPLATE_CHECK_WSL_IP",
        "origin_latitude_deg": float(config["latitude_deg"]),
        "origin_longitude_deg": float(config["longitude_deg"]),
        "ground_elevation_dem_m": round(origin_absolute, 3),
        "pedestrian_ground_truth": {
            "latitude_deg": float(pedestrian_lat),
            "longitude_deg": float(pedestrian_lon),
            "north_m": float(config["pedestrian_north_m"]),
            "west_m": float(config["pedestrian_west_m"]),
            "terrain_height_home_relative_m": round(height_local(
                heights, config, float(config["pedestrian_north_m"]),
                float(config["pedestrian_west_m"])), 3),
        },
        "height_range_relative_m": [float(heights.min()), float(heights.max())],
        "height_span_m": round(float(heights.max()-heights.min()), 3),
        "grid_spacing_m": float(config["grid_spacing_m"]),
        "native_dem_pixel_size_crs_units": source_meta["source_resolution_units"],
        "vertical_datum": "UNVERIFIED; inspect USGS product and SITL home altitude",
        "world_file": world_path.name, "terrain_file": terrain_path.name,
        "world_sha256": hashlib.sha256(world_path.read_bytes()).hexdigest(),
        "terrain_sha256": hashlib.sha256(terrain_path.read_bytes()).hexdigest(),
        "sitl_custom_location": f'{float(config["latitude_deg"]):.7f},{float(config["longitude_deg"]):.7f},{round(origin_absolute,1)},0',
        "important": [
            "Do not run this world while the stable demo world is active.",
            "The --sitl-address controllerArg may contain an obsolete WSL IP; check before starting.",
            "WorldInfo gpsCoordinateSystem is deliberately local to retain Webots-ArduPilot FDM compatibility.",
            "This is NOT a real-flight clearance or geolocation accuracy guarantee.",
            "The first export uses a resampled best-available service DEM; output spacing is not source accuracy.",
            "Land cover and tree locations are synthetic; OSM feature import is deferred.",
        ],
    }
    (output / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a STATIC Webots + backend terrain world offline")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=Path(__file__).parent / "assets" / "iris_wsl_camera_template.wbt")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "generated")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dem", type=Path)
    source.add_argument("--fetch-usgs", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--source-spacing-m", type=float, default=2)
    parser.add_argument("--sitl-ip", default=None, help="Current WSL IPv4 address used by Webots controller")
    args = parser.parse_args(argv)
    try:
        result = build_scenario(args.config, args.template, args.output_dir, dem_path=args.dem,
                                fetch=args.fetch_usgs, source_spacing_m=args.source_spacing_m,
                                force=args.force_download, sitl_ip=args.sitl_ip)
        manifest = json.loads((result / "scenario_manifest.json").read_text())
    except (TerrainBuildError, OSError, ValueError, rasterio.errors.RasterioError) as error:
        print(f"TERRAIN BUILD FAILED: {error}", file=sys.stderr)
        return 1
    print(f"Scenario output: {result}")
    print(f"Relief span: {manifest['height_span_m']:.2f} m (min/max: {manifest['height_range_relative_m']})")
    if manifest['height_span_m'] > 5:
        print("WARNING: relief exceeds preferred ~5 m. Reconsider center before any flight.")
    print(f"SITL --custom-location={manifest['sitl_custom_location']}")
    print("Generated only; Webots/SITL were NOT started or modified. Validate before flight.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
