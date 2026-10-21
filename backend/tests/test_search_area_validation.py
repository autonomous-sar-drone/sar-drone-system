from app.domain.models.search_area import GeoPoint, SearchArea
from app.geospatial.search_area_validation import (SearchAreaValidationReason, validate_search_area)


def make_search_area(coordinates: list[tuple[float, float]]) -> SearchArea:
    return SearchArea(
        vertices=tuple(
            GeoPoint(latitude_deg=latitude, longitude_deg=longitude)
            for latitude, longitude in coordinates
        )
    )


def test_valid_search_area() -> None:
    search_area = make_search_area([
        (30.0, -97.0),
        (30.1, -97.0),
        (30.1, -96.9),
        (30.0, -96.9),
    ])

    result = validate_search_area(search_area)

    assert result.valid is True
    assert result.reason is None


def test_rejects_too_few_vertices() -> None:
    search_area = make_search_area([
        (30.0, -97.0),
        (30.1, -97.0),
    ])

    result = validate_search_area(search_area)

    assert result.valid is False
    assert result.reason == SearchAreaValidationReason.TOO_FEW_VERTICES


def test_rejects_duplicate_vertices() -> None:
    search_area = make_search_area([
        (30.0, -97.0),
        (30.1, -97.0),
        (30.1, -96.9),
        (30.0, -97.0),
    ])

    result = validate_search_area(search_area)

    assert result.valid is False
    assert result.reason == SearchAreaValidationReason.DUPLICATE_VERTICES


def test_rejects_degenerate_area() -> None:
    search_area = make_search_area([
        (30.0, -97.0),
        (31.0, -96.0),
        (32.0, -95.0),
    ])

    result = validate_search_area(search_area)

    assert result.valid is False
    assert result.reason == SearchAreaValidationReason.DEGENERATE_AREA


def test_rejects_self_intersection() -> None:
    search_area = make_search_area([
        (30.0, -97.0),
        (30.1, -96.9),
        (30.0, -96.9),
        (30.1, -97.0),
    ])

    result = validate_search_area(search_area)

    assert result.valid is False
    assert result.reason == SearchAreaValidationReason.SELF_INTERSECTION