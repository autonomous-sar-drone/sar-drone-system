from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.models.route import AltitudeReference, Waypoint
from app.geospatial.terrain_grid import TerrainGrid
from app.geospatial.terrain_navigation import build_safe_transit


@dataclass(frozen=True)
class RouteDepartureAnchor:
    """Search location and cursor captured before the first diversion."""

    position: Waypoint
    unfinished_target: Waypoint
    completed_count: int


@dataclass(frozen=True)
class TerrainRelocation:
    """A route split at mandatory arrival checks, never at terrain samples."""

    commands: tuple[Waypoint, ...]
    segment_ends: tuple[int, ...]
    climb_target: Waypoint | None


def plan_terrain_relocation(
    terrain: TerrainGrid,
    departure: Waypoint,
    destination: Waypoint,
    desired_agl_m: float,
    spacing_m: float = 5.0,
    horizontal_speed_mps: float = 2.0,
    climb_speed_mps: float = 0.65,
    reserve_m: float = 1.0,
) -> TerrainRelocation:
    """Plan a corridor-aware transfer, climbing in place before moving if needed.

    End position is exact. If its capture-time altitude is below corridor
    clearance but still above the nominal AGL, descend only after arrival.
    An unsafe/unknown start, destination, or intervening corridor is refused.
    """
    if departure.altitude_reference != AltitudeReference.HOME_RELATIVE or (
        destination.altitude_reference != AltitudeReference.HOME_RELATIVE
    ):
        raise ValueError("Relocation needs HOME_RELATIVE altitude")
    if not all(math.isfinite(value) for value in (
        departure.altitude_m, destination.altitude_m,
    )):
        raise ValueError("Relocation altitude is invalid")

    ground_start = terrain.height_geo(departure.latitude_deg, departure.longitude_deg)
    ground_end = terrain.height_geo(destination.latitude_deg, destination.longitude_deg)
    if departure.altitude_m < ground_start + 5.0 - 1e-5:
        raise ValueError("Relocation start is below the supported manual altitude envelope")
    if destination.altitude_m < ground_end + desired_agl_m - 1e-5:
        raise ValueError("Destination altitude is below the nominal terrain clearance")

    transit = list(build_safe_transit(
        terrain, departure.latitude_deg, departure.longitude_deg,
        destination.latitude_deg, destination.longitude_deg,
        desired_agl_m=desired_agl_m, spacing_m=spacing_m,
        horizontal_speed_mps=horizontal_speed_mps,
        climb_speed_mps=climb_speed_mps, reserve_m=reserve_m,
    ))
    if not transit:
        raise ValueError("Relocation corridor is missing")

    # A higher arrival altitude must be anticipated along the approach,
    # rather than demanding a sudden climb at the destination.
    descent_per_m = climb_speed_mps / horizontal_speed_mps
    if destination.altitude_m > transit[-1].altitude_m:
        end = transit[-1]
        transit[-1] = Waypoint(end.latitude_deg, end.longitude_deg,
                               destination.altitude_m, AltitudeReference.HOME_RELATIVE)
        for index in range(len(transit) - 2, -1, -1):
            current, following = transit[index], transit[index + 1]
            n0, w0 = terrain.geo_to_local(current.latitude_deg, current.longitude_deg)
            n1, w1 = terrain.geo_to_local(following.latitude_deg, following.longitude_deg)
            maximum_climb = descent_per_m * math.hypot(n1 - n0, w1 - w0)
            transit[index] = Waypoint(current.latitude_deg, current.longitude_deg,
                                      max(current.altitude_m, following.altitude_m - maximum_climb),
                                      AltitudeReference.HOME_RELATIVE)

    # Do not descend faster than the planned vertical rate from an unusually
    # high manual position. Conversely, a low starting altitude needs a climb
    # at the current coordinates before the horizontal lookahead begins.
    start_required = max(ground_start + desired_agl_m + reserve_m,
                         transit[0].altitude_m)
    climb_target = None
    if departure.altitude_m < start_required - 0.75:
        climb_target = Waypoint(departure.latitude_deg, departure.longitude_deg,
                                start_required, AltitudeReference.HOME_RELATIVE)
    prior = climb_target or departure
    for index, point in enumerate(transit):
        n0, w0 = terrain.geo_to_local(prior.latitude_deg, prior.longitude_deg)
        n1, w1 = terrain.geo_to_local(point.latitude_deg, point.longitude_deg)
        distance_m = math.hypot(n1 - n0, w1 - w0)
        altitude = max(point.altitude_m, prior.altitude_m - descent_per_m * distance_m)
        transit[index] = Waypoint(point.latitude_deg, point.longitude_deg, altitude,
                                  AltitudeReference.HOME_RELATIVE)
        prior = transit[index]

    commands = []
    ends = []
    if climb_target is not None:
        commands.append(climb_target)
        ends.append(len(commands) - 1)
    commands.extend(transit)
    ends.append(len(commands) - 1)

    # Historical camera-viewpoint altitude is reached at the destination,
    # never by descending while crossing potentially higher terrain.
    if abs(commands[-1].altitude_m - destination.altitude_m) > 0.75:
        commands.append(destination)
        ends.append(len(commands) - 1)

    if len(commands) > 3000:
        raise ValueError("Terrain relocation exceeds the command limit")
    return TerrainRelocation(tuple(commands), tuple(ends), climb_target)


def plan_terrain_relocation_chain(
    terrain: TerrainGrid,
    departure: Waypoint,
    destinations: tuple[Waypoint, ...],
    desired_agl_m: float,
    spacing_m: float = 5.0,
) -> TerrainRelocation:
    if not destinations:
        raise ValueError("Relocation must have a destination")
    commands: list[Waypoint] = []
    ends: list[int] = []
    initial_climb = None
    current = departure
    for dest in destinations:
        section = plan_terrain_relocation(
            terrain, current, dest, desired_agl_m, spacing_m,
        )
        if not commands:
            initial_climb = section.climb_target
        offset = len(commands)
        commands.extend(section.commands)
        ends.extend(offset + i for i in section.segment_ends)
        current = commands[-1]
    if len(commands) > 3000:
        raise ValueError("Terrain relocation exceeds the command limit")
    return TerrainRelocation(tuple(commands), tuple(ends), initial_climb)
