from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyproj import CRS, Transformer


@lru_cache(maxsize=8)
def _local_transformers(latitude_deg: float, longitude_deg: float) -> tuple[Transformer, Transformer]:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={latitude_deg} +lon_0={longitude_deg} +datum=WGS84 +units=m +no_defs"
    )
    return (
        Transformer.from_crs("EPSG:4326", crs, always_xy=True),
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True),
    )


@dataclass(frozen=True)
class TerrainGrid:
    scenario_id: str
    origin_latitude_deg: float
    origin_longitude_deg: float
    origin_ground_elevation_dem_m: float
    width_m: float
    height_m: float
    spacing_m: float
    heights: tuple[tuple[float, ...], ...]

    @classmethod
    def load(cls, path: str | Path) -> TerrainGrid:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != "sar-terrain-grid-v1":
            raise ValueError("Unsupported terrain grid format")
        if data.get("world_x_is_north") is not True or data.get("world_y_is_west") is not True:
            raise ValueError("Terrain grid axes do not match Webots")

        rows = tuple(tuple(float(value) for value in row) for row in data["heights_home_relative_m"])
        grid = cls(
            scenario_id=str(data["scenario_id"]),
            origin_latitude_deg=float(data["origin_latitude_deg"]),
            origin_longitude_deg=float(data["origin_longitude_deg"]),
            origin_ground_elevation_dem_m=float(data["origin_ground_elevation_dem_m"]),
            width_m=float(data["width_m"]),
            height_m=float(data["height_m"]),
            spacing_m=float(data["grid_spacing_m"]),
            heights=rows,
        )
        grid.validate()
        if int(data["x_dimension"]) != len(rows[0]) or int(data["y_dimension"]) != len(rows):
            raise ValueError("Terrain grid dimensions differ from header")
        return grid

    def validate(self) -> None:
        if not self.scenario_id:
            raise ValueError("Terrain scenario ID is missing")
        if not (
            math.isfinite(self.origin_latitude_deg)
            and -90 < self.origin_latitude_deg < 90
            and math.isfinite(self.origin_longitude_deg)
            and -180 < self.origin_longitude_deg < 180
            and math.isfinite(self.origin_ground_elevation_dem_m)
        ):
            raise ValueError("Invalid terrain origin")
        if not (
            all(math.isfinite(value) and value > 0 for value in (self.width_m, self.height_m, self.spacing_m))
        ):
            raise ValueError("Invalid terrain dimensions")
        if len(self.heights) < 2 or len(self.heights[0]) < 2:
            raise ValueError("Terrain grid must contain at least two rows and columns")
        columns = len(self.heights[0])
        if any(len(row) != columns or not all(math.isfinite(value) for value in row) for row in self.heights):
            raise ValueError("Terrain grid has inconsistent or missing elevations")
        if (
            abs((columns - 1) * self.spacing_m - self.width_m) > 0.001
            or abs((len(self.heights) - 1) * self.spacing_m - self.height_m) > 0.001
        ):
            raise ValueError("Terrain grid dimensions and spacing disagree")
        if abs(self.height_local(0.0, 0.0)) > 0.002:
            raise ValueError("Terrain at home must be normalized to zero")

    def geo_to_local(self, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
        if not all(math.isfinite(value) for value in (latitude_deg, longitude_deg)):
            raise ValueError("Non-finite geographic coordinate")
        to_local, _ = _local_transformers(self.origin_latitude_deg, self.origin_longitude_deg)
        east, north = to_local.transform(longitude_deg, latitude_deg)
        return float(north), float(-east)

    def local_to_geo(self, north_m: float, west_m: float) -> tuple[float, float]:
        if not all(math.isfinite(value) for value in (north_m, west_m)):
            raise ValueError("Non-finite local coordinate")
        _, to_geo = _local_transformers(self.origin_latitude_deg, self.origin_longitude_deg)
        longitude, latitude = to_geo.transform(-west_m, north_m)
        return float(latitude), float(longitude)

    def height_local(self, north_m: float, west_m: float) -> float:
        if not all(math.isfinite(value) for value in (north_m, west_m)):
            raise ValueError("Non-finite local coordinate")
        i = (north_m + self.width_m / 2) / self.spacing_m
        j = (west_m + self.height_m / 2) / self.spacing_m
        max_i = len(self.heights[0]) - 1
        max_j = len(self.heights) - 1
        if not (-1e-6 <= i <= max_i + 1e-6 and -1e-6 <= j <= max_j + 1e-6):
            raise ValueError("Point outside the terrain grid")

        i = min(max(0.0, i), float(max_i))
        j = min(max(0.0, j), float(max_j))
        i0 = min(int(i), max_i - 1)
        j0 = min(int(j), max_j - 1)
        u, v = i - i0, j - j0
        h00, h10 = self.heights[j0][i0], self.heights[j0][i0 + 1]
        h01, h11 = self.heights[j0 + 1][i0], self.heights[j0 + 1][i0 + 1]
        return (1 - u) * (1 - v) * h00 + u * (1 - v) * h10 + (1 - u) * v * h01 + u * v * h11

    def height_geo(self, latitude_deg: float, longitude_deg: float) -> float:
        return self.height_local(*self.geo_to_local(latitude_deg, longitude_deg))


def load_configured_terrain(path: str | Path | None) -> TerrainGrid | None:
    return TerrainGrid.load(path) if path else None
