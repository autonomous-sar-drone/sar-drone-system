from pyproj import Geod

from app.domain.models.search_area import GeoPoint


_WGS84_GEOD = Geod(ellps="WGS84")


def geodesic_distance_m(first: GeoPoint, second: GeoPoint) -> float:
    #Return the surface distance between two WGS84 coordinates in meters
    _, _, distance_m = _WGS84_GEOD.inv(
        first.longitude_deg,
        first.latitude_deg,
        second.longitude_deg,
        second.latitude_deg,
    )

    return float(distance_m)
