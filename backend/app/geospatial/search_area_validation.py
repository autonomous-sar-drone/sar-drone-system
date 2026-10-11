from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import Polygon

from app.domain.models.search_area import SearchArea


class SearchAreaValidationReason(StrEnum):
    TOO_FEW_VERTICES = "TOO_FEW_VERTICES"
    DUPLICATE_VERTICES = "DUPLICATE_VERTICES"
    SELF_INTERSECTION = "SELF_INTERSECTION"
    DEGENERATE_AREA = "DEGENERATE_AREA"


@dataclass(frozen=True)
class SearchAreaValidationResult:
    valid: bool
    reason: SearchAreaValidationReason | None


def validate_search_area(search_area: SearchArea) -> SearchAreaValidationResult:
    if len(search_area.vertices) < 3:
        return SearchAreaValidationResult(
            valid=False,
            reason=SearchAreaValidationReason.TOO_FEW_VERTICES,
        )

    unique_vertices = {
        (vertex.latitude_deg, vertex.longitude_deg)
        for vertex in search_area.vertices
    }

    if len(unique_vertices) != len(search_area.vertices):
        return SearchAreaValidationResult(
            valid=False,
            reason=SearchAreaValidationReason.DUPLICATE_VERTICES,
        )

    # Shapely uses planar (x, y) ordering, so longitude comes before latitude.
    coordinates = [
        (vertex.longitude_deg, vertex.latitude_deg)
        for vertex in search_area.vertices
    ]
    polygon = Polygon(coordinates)

    # Distinguishes collinear geometry from a self crossing polygon with zero area.
    if polygon.convex_hull.area == 0.0:
        return SearchAreaValidationResult(
            valid=False,
            reason=SearchAreaValidationReason.DEGENERATE_AREA,
        )

    if not polygon.is_valid:
        return SearchAreaValidationResult(
            valid=False,
            reason=SearchAreaValidationReason.SELF_INTERSECTION,
        )

    return SearchAreaValidationResult(valid=True, reason=None)