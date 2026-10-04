from dataclasses import dataclass


@dataclass(frozen=True)
class GeoPoint:
    latitude_deg: float
    longitude_deg: float

@dataclass(frozen=True)
class SearchArea:
    vertices: tuple[GeoPoint, ...]