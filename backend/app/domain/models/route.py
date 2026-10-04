from dataclasses import dataclass
from enum import StrEnum


class AltitudeReference(StrEnum):
    HOME_RELATIVE = "HOME_RELATIVE"


@dataclass(frozen=True)
class Waypoint:
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    altitude_reference: AltitudeReference = AltitudeReference.HOME_RELATIVE


@dataclass(frozen=True)
class Route:
    waypoints: tuple[Waypoint, ...]


@dataclass(frozen=True)
class PlanningSettings:
    search_altitude_m: float
    lane_spacing_m: float


@dataclass(frozen=True)
class RouteMetrics:
    distance_to_start_m: float
    route_distance_m: float