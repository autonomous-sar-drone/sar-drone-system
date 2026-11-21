import json
import math
import re
from pathlib import Path

import pytest

from app.geospatial.terrain_grid import TerrainGrid, load_configured_terrain


TERRAIN_ROOT = Path(__file__).resolve().parents[2] / "tools" / "terrain"
GRID_PATH = TERRAIN_ROOT / "reference" / "manor_prairie_240_flight_v2_terrain.json"
WORLD_PATH = TERRAIN_ROOT / "worlds" / "manor_prairie_240_roads_buildings_v4.wbt"


def sample_grid(tmp_path):
    data = {
        "format": "sar-terrain-grid-v1",
        "scenario_id": "unit-test",
        "origin_latitude_deg": 30.3678,
        "origin_longitude_deg": -97.5396,
        "origin_ground_elevation_dem_m": 166.469,
        "world_x_is_north": True,
        "world_y_is_west": True,
        "width_m": 8,
        "height_m": 8,
        "grid_spacing_m": 4,
        "x_dimension": 3,
        "y_dimension": 3,
        "heights_home_relative_m": [[-2, -1, 0], [-1, 0, 1], [0, 1, 2]],
    }
    path = tmp_path / "terrain.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return data, path


def test_terrain_loading_is_optional_and_invalid_path_fails(tmp_path):
    assert load_configured_terrain("") is None
    assert load_configured_terrain(None) is None
    data, path = sample_grid(tmp_path)
    loaded = load_configured_terrain(path)
    assert loaded is not None
    assert loaded.scenario_id == "unit-test"
    assert loaded.height_local(2, 2) == pytest.approx(1.0)
    with pytest.raises(FileNotFoundError):
        load_configured_terrain(tmp_path / "missing.json")


def test_terrain_projection_and_bilinear_height(tmp_path):
    _, path = sample_grid(tmp_path)
    grid = TerrainGrid.load(path)
    for north, west in ((0, 0), (2, 2), (4, -4), (-4, 4), (1.25, -3.0)):
        lat, lon = grid.local_to_geo(north, west)
        assert grid.geo_to_local(lat, lon) == pytest.approx((north, west), abs=1e-6)
        assert grid.height_geo(lat, lon) == pytest.approx(grid.height_local(north, west), abs=1e-6)
    assert grid.height_local(2, 2) == pytest.approx(1)
    assert grid.height_local(-4, -4) == pytest.approx(-2)
    assert grid.height_local(4, 4) == pytest.approx(2)
    assert grid.geo_to_local(grid.origin_latitude_deg + 0.0001, grid.origin_longitude_deg)[0] > 0
    assert grid.geo_to_local(grid.origin_latitude_deg, grid.origin_longitude_deg - 0.0001)[1] > 0


def test_terrain_rejects_outside_and_nonfinite_coordinates(tmp_path):
    _, path = sample_grid(tmp_path)
    grid = TerrainGrid.load(path)
    for north, west in ((4.01, 0), (0, -4.01), (float("nan"), 0), (float("inf"), 0)):
        with pytest.raises(ValueError):
            grid.height_local(north, west)
    with pytest.raises(ValueError):
        grid.height_geo(float("nan"), grid.origin_longitude_deg)
    with pytest.raises(ValueError):
        grid.local_to_geo(0, float("inf"))


@pytest.mark.parametrize("change,error", [
    ({"format": "bad-format"}, "format"),
    ({"world_x_is_north": False}, "axes"),
    ({"x_dimension": 4}, "header"),
    ({"grid_spacing_m": 5}, "spacing"),
    ({"origin_latitude_deg": 95}, "origin"),
    ({"heights_home_relative_m": [[1, 2], [3]]}, "inconsistent"),
    ({"heights_home_relative_m": [[0, 0, 0], [0, float("nan"), 0], [0, 0, 0]]}, "missing elevations"),
    ({"heights_home_relative_m": [[0, 0, 0], [0, 1, 0], [0, 0, 0]]}, "normalized"),
])
def test_invalid_terrain_metadata_is_rejected(tmp_path, change, error):
    data, path = sample_grid(tmp_path)
    data.update(change)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        TerrainGrid.load(path)


def test_manor_v4_world_matches_backend_heights():
    grid = TerrainGrid.load(GRID_PATH)
    assert grid.scenario_id == "manor_prairie_240_flight_v2"
    assert grid.width_m == grid.height_m == 240
    assert grid.spacing_m == 4
    assert len(grid.heights) == len(grid.heights[0]) == 61
    assert grid.height_local(0, 0) == pytest.approx(0)

    world = WORLD_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"\bDEF\s+SAR_ELEVATION_GRID\s+ElevationGrid\s*\{\s*height\s*\[([^\]]*)\]"
        r"\s*xDimension\s+(\d+)\s*xSpacing\s+([\d.]+)"
        r"\s*yDimension\s+(\d+)\s*ySpacing\s+([\d.]+)",
        world, re.S,
    )
    assert match, "Saved Manor V4 world has no expected elevation grid"
    heights = [float(value) for value in match.group(1).replace(",", " ").split()]
    assert (int(match.group(2)), float(match.group(3)), int(match.group(4)), float(match.group(5))) == (61, 4, 61, 4)
    assert heights == [value for row in grid.heights for value in row]
    assert len(heights) == 3721
    assert math.isclose(max(heights) - min(heights), 4.504, abs_tol=1e-6)
