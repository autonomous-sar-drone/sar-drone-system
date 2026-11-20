import math

from app.geospatial.terrain_grid import TerrainGrid


def safe_home_relative_rtl_altitude(
    terrain: TerrainGrid,
    minimum_altitude_m: float = 50.0,
    required_clearance_m: float = 20.0,
    terrain_margin_m: float = 5.0,
) -> float:
    """Choose a home-relative RTL altitude covering the entire configured terrain."""
    if not all(
        math.isfinite(value) and value > 0
        for value in (minimum_altitude_m, required_clearance_m, terrain_margin_m)
    ):
        raise ValueError("RTL altitude and clearance limits must be positive")

    highest_ground_m = max(max(row) for row in terrain.heights)
    required_altitude_m = max(
        minimum_altitude_m,
        highest_ground_m + required_clearance_m + terrain_margin_m,
    )
    # Round upward to an easy-to-audit RTL setting.
    return float(math.ceil((required_altitude_m - 1e-9) / 5.0) * 5)
