from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.models.route import AltitudeReference, Route, Waypoint
from app.geospatial.terrain_grid import TerrainGrid


@dataclass(frozen=True)
class NavigationCommand:
    target: Waypoint
    visible_index: int
    completes_visible: bool


def _validate_settings(
    desired_agl_m: float,
    spacing_m: float,
    horizontal_speed_mps: float,
    climb_speed_mps: float,
    reserve_m: float,
) -> None:
    if not 5 <= desired_agl_m <= 120 or not math.isfinite(desired_agl_m):
        raise ValueError("Requested terrain clearance is invalid")
    if not 1 <= spacing_m <= 20 or not math.isfinite(spacing_m):
        raise ValueError("Intermediate waypoint spacing must be between 1 and 20 m")
    if not 0 < horizontal_speed_mps <= 5 or not math.isfinite(horizontal_speed_mps):
        raise ValueError("Planning horizontal speed is invalid")
    if not 0 < climb_speed_mps <= 2 or not math.isfinite(climb_speed_mps):
        raise ValueError("Planning climb rate is invalid")
    if not 0 <= reserve_m <= 5 or not math.isfinite(reserve_m):
        raise ValueError("Terrain clearance reserve is invalid")


def build_safe_transit(
    terrain: TerrainGrid,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    desired_agl_m: float,
    spacing_m: float = 5.0,
    horizontal_speed_mps: float = 2.0,
    climb_speed_mps: float = 0.65,
    reserve_m: float = 1.0,
    corridor_half_width_m: float = 1.5,
    lookahead_s: float = 3.0,
) -> tuple[Waypoint, ...]:
    _validate_settings(desired_agl_m, spacing_m, horizontal_speed_mps, climb_speed_mps, reserve_m)
    if not 0 <= corridor_half_width_m <= 3 or not math.isfinite(corridor_half_width_m):
        raise ValueError("Terrain corridor width is invalid")
    if not 0 <= lookahead_s <= 10 or not math.isfinite(lookahead_s):
        raise ValueError("Terrain lookahead is invalid")

    n0, w0 = terrain.geo_to_local(start_lat, start_lon)
    n1, w1 = terrain.geo_to_local(end_lat, end_lon)
    length = math.hypot(n1 - n0, w1 - w0)
    if not math.isfinite(length):
        raise ValueError("Invalid terrain segment length")
    if length > 2000:
        raise ValueError("Terrain transit leg exceeds 2 km")

    # Evaluate the full corridor at a finer resolution than command spacing.
    sample_spacing = min(terrain.spacing_m / 2, spacing_m, 2.0)
    count = max(1, math.ceil(length / sample_spacing))
    if count > 3000:
        raise ValueError("Terrain transit has too many samples")
    step_len = length / count
    direction_n = (n1 - n0) / length if length else 0.0
    direction_w = (w1 - w0) / length if length else 0.0

    ground = []
    for index in range(count + 1):
        fraction = index / count
        north = n0 + fraction * (n1 - n0)
        west = w0 + fraction * (w1 - w0)
        elevations = [terrain.height_local(north, west)]
        if length > 0 and corridor_half_width_m:
            elevations.extend((
                terrain.height_local(north - direction_w * corridor_half_width_m,
                                     west + direction_n * corridor_half_width_m),
                terrain.height_local(north + direction_w * corridor_half_width_m,
                                     west - direction_n * corridor_half_width_m),
            ))
        ground.append(max(elevations))

    clearance = [desired_agl_m + reserve_m + height for height in ground]
    horizon = math.ceil(lookahead_s * horizontal_speed_mps / sample_spacing)
    ahead = [max(clearance[i:min(len(clearance), i + horizon + 1)])
             for i in range(len(clearance))]
    climb_per_m = climb_speed_mps / horizontal_speed_mps
    height_profile = ahead[:]
    for index in range(count - 1, -1, -1):
        height_profile[index] = max(height_profile[index],
                                    height_profile[index + 1] - climb_per_m * step_len)
    for index in range(1, count + 1):
        height_profile[index] = max(height_profile[index],
                                    height_profile[index - 1] - climb_per_m * step_len)

    command_count = max(1, math.ceil(length / spacing_m))
    commands = []
    for command_index in range(1, command_count + 1):
        fraction = command_index / command_count
        sample_index = min(count, math.ceil(fraction * count))
        north = n0 + fraction * (n1 - n0)
        west = w0 + fraction * (w1 - w0)
        latitude, longitude = terrain.local_to_geo(north, west)
        if command_index == command_count:
            latitude, longitude = end_lat, end_lon
        altitude = max(height_profile[sample_index],
                       desired_agl_m + reserve_m + terrain.height_geo(latitude, longitude))
        commands.append(Waypoint(latitude, longitude, altitude,
                                 AltitudeReference.HOME_RELATIVE))
    return tuple(commands)


def build_terrain_navigation(
    terrain: TerrainGrid,
    route: Route,
    launch_lat: float,
    launch_lon: float,
    desired_agl_m: float,
    spacing_m: float = 5.0,
    horizontal_speed_mps: float = 2.0,
    climb_speed_mps: float = 0.65,
    reserve_m: float = 1.0,
) -> tuple[NavigationCommand, ...]:
    if not route.waypoints:
        raise ValueError("Cannot navigate an empty search route")
    start_n, start_w = terrain.geo_to_local(launch_lat, launch_lon)
    if math.hypot(start_n, start_w) > 15:
        raise ValueError("Aircraft is not near the selected terrain origin; check SITL location")
    commands = []
    prev_lat, prev_lon = launch_lat, launch_lon
    for index, visible in enumerate(route.waypoints):
        if visible.altitude_reference != AltitudeReference.HOME_RELATIVE:
            raise ValueError("Terrain navigation requires HOME_RELATIVE route targets")
        if abs(visible.altitude_m - desired_agl_m) > 1e-4:
            raise ValueError("Search waypoint altitudes differ from requested AGL")
        transit = build_safe_transit(
            terrain, prev_lat, prev_lon, visible.latitude_deg, visible.longitude_deg,
            desired_agl_m, spacing_m, horizontal_speed_mps, climb_speed_mps, reserve_m,
        )
        commands.extend(NavigationCommand(
            target=target, visible_index=index, completes_visible=step == len(transit) - 1,
        ) for step, target in enumerate(transit))
        if len(commands) > 3000:
            raise ValueError("Search route exceeds the terrain command limit")
        prev_lat, prev_lon = visible.latitude_deg, visible.longitude_deg

    climb_per_m = climb_speed_mps / horizontal_speed_mps
    adjusted = [command.target.altitude_m for command in commands]
    for index in range(len(commands) - 2, -1, -1):
        current = commands[index].target
        following = commands[index + 1].target
        north_a, west_a = terrain.geo_to_local(current.latitude_deg, current.longitude_deg)
        north_b, west_b = terrain.geo_to_local(following.latitude_deg, following.longitude_deg)
        length = math.hypot(north_b - north_a, west_b - west_a)
        adjusted[index] = max(adjusted[index], adjusted[index + 1] - climb_per_m * length)
    for index in range(1, len(commands)):
        current = commands[index - 1].target
        following = commands[index].target
        north_a, west_a = terrain.geo_to_local(current.latitude_deg, current.longitude_deg)
        north_b, west_b = terrain.geo_to_local(following.latitude_deg, following.longitude_deg)
        length = math.hypot(north_b - north_a, west_b - west_a)
        adjusted[index] = max(adjusted[index], adjusted[index - 1] - climb_per_m * length)
    return tuple(NavigationCommand(
        target=Waypoint(c.target.latitude_deg, c.target.longitude_deg,
                        adjusted[index], c.target.altitude_reference),
        visible_index=c.visible_index, completes_visible=c.completes_visible,
    ) for index, c in enumerate(commands))


@dataclass(frozen=True)
class LookaheadTarget:
    target: Waypoint
    next_index: int
    cross_track_m: float


def terrain_lookahead_target(
    terrain: TerrainGrid,
    latitude_deg: float,
    longitude_deg: float,
    start: Waypoint,
    points: tuple[Waypoint, ...],
    lookahead_m: float = 8.0,
) -> LookaheadTarget:
    if not points or not math.isfinite(lookahead_m) or not 3 <= lookahead_m <= 30:
        raise ValueError("Invalid terrain lookahead path")

    n0, w0 = terrain.geo_to_local(start.latitude_deg, start.longitude_deg)
    n1, w1 = terrain.geo_to_local(points[-1].latitude_deg, points[-1].longitude_deg)
    n, w = terrain.geo_to_local(latitude_deg, longitude_deg)
    length = math.hypot(n1 - n0, w1 - w0)
    if length < 1e-5:
        return LookaheadTarget(points[-1], len(points) - 1, math.hypot(n - n1, w - w1))

    unit_n, unit_w = (n1 - n0) / length, (w1 - w0) / length
    along = max(0.0, min(length, (n - n0) * unit_n + (w - w0) * unit_w))
    cross_track = abs((n - n0) * unit_w - (w - w0) * unit_n)
    distances = []
    for point in points:
        point_n, point_w = terrain.geo_to_local(point.latitude_deg, point.longitude_deg)
        distances.append(min(length, max(0.0,
                             (point_n - n0) * unit_n + (point_w - w0) * unit_w)))
    if any(a > b + 0.01 for a, b in zip(distances, distances[1:])):
        raise ValueError("Terrain path points are out of order")

    next_index = 0
    while next_index < len(points) - 1 and distances[next_index] <= along + 0.05:
        next_index += 1

    aim = min(length, along + lookahead_m)
    hi = 0
    while hi < len(points) - 1 and distances[hi] < aim:
        hi += 1
    low_dist = distances[hi - 1] if hi else 0.0
    low_alt = points[hi - 1].altitude_m if hi else start.altitude_m
    high_dist = distances[hi]
    fraction = max(0.0, min(1.0, (aim - low_dist) / max(1e-9, high_dist - low_dist)))
    altitude = low_alt + (points[hi].altitude_m - low_alt) * fraction
    aim_n = n0 + unit_n * aim
    aim_w = w0 + unit_w * aim
    latitude, longitude = terrain.local_to_geo(aim_n, aim_w)
    if aim >= length - 1e-5:
        latitude, longitude = points[-1].latitude_deg, points[-1].longitude_deg
    return LookaheadTarget(Waypoint(latitude, longitude, altitude,
                                    AltitudeReference.HOME_RELATIVE),
                           next_index, cross_track)
