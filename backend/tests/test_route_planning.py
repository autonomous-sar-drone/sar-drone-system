import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon

from app.domain.models.route import AltitudeReference, PlanningSettings
from app.domain.models.search_area import GeoPoint, SearchArea
from app.geospatial.route_planning import (
    _generate_sweep_route,
    _shortest_path_inside_polygon,
    _sweep_y_values,
    generate_route,
)


TEST_CENTER_LATITUDE = 30.2672
TEST_CENTER_LONGITUDE = -97.7431
GEOMETRY_TOLERANCE_M = 1e-6

TEST_LOCAL_CRS = CRS.from_proj4(
    f"+proj=aeqd +lat_0={TEST_CENTER_LATITUDE} "
    f"+lon_0={TEST_CENTER_LONGITUDE} "
    "+datum=WGS84 +units=m +no_defs"
)
TEST_GEOGRAPHIC_CRS = CRS.from_epsg(4326)

TO_GEOGRAPHIC = Transformer.from_crs(
    TEST_LOCAL_CRS,
    TEST_GEOGRAPHIC_CRS,
    always_xy=True,
)
TO_LOCAL = Transformer.from_crs(
    TEST_GEOGRAPHIC_CRS,
    TEST_LOCAL_CRS,
    always_xy=True,
)


def make_geo_point(x: float, y: float) -> GeoPoint:
    longitude_deg, latitude_deg = TO_GEOGRAPHIC.transform(x, y)

    return GeoPoint(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
    )


def make_search_area(vertices: list[tuple[float, float]]) -> SearchArea:
    return SearchArea(
        vertices=tuple(
            make_geo_point(x, y)
            for x, y in vertices
        )
    )


def assert_route_stays_inside_polygon(
    route: list[tuple[float, float]],
    polygon: Polygon,
    tolerance_m: float = GEOMETRY_TOLERANCE_M,
) -> None:
    assert len(route) >= 2

    # The small buffer only handles floating point error at polygon boundaries.
    allowed_area = polygon.buffer(tolerance_m)

    for point in route:
        assert allowed_area.covers(Point(point))

    # Checking the full segment matters because legal endpoints can still
    # have an illegal straight path between them.
    for index in range(1, len(route)):
        segment = LineString([route[index - 1], route[index]])
        assert allowed_area.covers(segment)


@pytest.mark.parametrize(
    ("height", "expected_passes"),
    [
        (10.0, 1),
        (20.0, 1),
        (20.1, 2),
    ],
)
def test_thin_rectangle_uses_correct_number_of_sweeps(
    height: float,
    expected_passes: int,
) -> None:
    polygon = Polygon(
        [
            (0.0, 0.0),
            (80.0, 0.0),
            (80.0, height),
            (0.0, height),
        ]
    )

    route = _generate_sweep_route(
        polygon=polygon,
        lane_spacing_m=20.0,
    )

    # A rectangle gives one start and one end point for every sweep.
    assert len(route) == expected_passes * 2
    assert_route_stays_inside_polygon(route, polygon)


def test_sweep_rows_do_not_exceed_requested_spacing() -> None:
    min_y = 0.0
    max_y = 45.0
    lane_spacing_m = 20.0

    y_values = _sweep_y_values(
        min_y,
        max_y,
        lane_spacing_m,
    )

    # A 45 meter area needs three passes when spacing is capped at 20 meters.
    assert len(y_values) == 3

    for index in range(1, len(y_values)):
        assert y_values[index] - y_values[index - 1] <= lane_spacing_m

    # The outer edges should also stay within half a lane spacing of a pass.
    assert y_values[0] - min_y <= lane_spacing_m / 2
    assert max_y - y_values[-1] <= lane_spacing_m / 2


@pytest.mark.parametrize(
    "vertices",
    [
        pytest.param(
            [
                (0.0, 0.0),
                (80.0, 0.0),
                (80.0, 40.0),
                (0.0, 40.0),
            ],
            id="rectangle",
        ),
        pytest.param(
            [
                (0.0, 0.0),
                (80.0, 0.0),
                (80.0, 15.0),
                (25.0, 15.0),
                (25.0, 30.0),
                (65.0, 30.0),
                (65.0, 45.0),
                (25.0, 45.0),
                (25.0, 60.0),
                (80.0, 60.0),
                (80.0, 75.0),
                (0.0, 75.0),
            ],
            id="e-shape",
        ),
        pytest.param(
            [
                (0.0, 0.0),
                (80.0, 0.0),
                (100.0, 40.0),
                (50.0, 80.0),
                (0.0, 40.0),
            ],
            id="pentagon",
        ),
        pytest.param(
            [
                (0.0, 100.0),
                (120.0, 100.0),
                (120.0, 0.0),
                (100.0, 70.0),
                (80.0, 0.0),
                (60.0, 70.0),
                (40.0, 0.0),
                (20.0, 70.0),
                (0.0, 0.0),
            ],
            id="deep-w",
        ),
        pytest.param(
            [
                (0.0, 0.0),
                (120.0, 0.0),
                (120.0, 100.0),
                (100.0, 30.0),
                (80.0, 100.0),
                (60.0, 30.0),
                (40.0, 100.0),
                (20.0, 30.0),
                (0.0, 100.0),
            ],
            id="deep-inverted-w",
        ),
    ],
)
def test_sweep_route_stays_inside_common_polygon_shapes(
    vertices: list[tuple[float, float]],
) -> None:
    polygon = Polygon(vertices)

    assert polygon.is_valid

    route = _generate_sweep_route(
        polygon=polygon,
        lane_spacing_m=20.0,
    )

    # The same route safety rule should hold for simple and deeply concave shapes.
    assert_route_stays_inside_polygon(route, polygon)


def test_connector_routes_around_concavity() -> None:
    polygon = Polygon(
        [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (70.0, 100.0),
            (70.0, 30.0),
            (30.0, 30.0),
            (30.0, 100.0),
            (0.0, 100.0),
        ]
    )

    start = (10.0, 90.0)
    end = (90.0, 90.0)

    # The direct connection crosses the open middle of the U shape.
    assert not polygon.covers(LineString([start, end]))

    path = _shortest_path_inside_polygon(
        start=start,
        end=end,
        polygon=polygon,
    )

    assert path[0] == start
    assert path[-1] == end

    # More than two points means the connector had to route around the concavity.
    assert len(path) > 2
    assert_route_stays_inside_polygon(path, polygon)


def test_generate_route_returns_waypoints_and_metrics() -> None:
    search_area = make_search_area(
        [
            (0.0, 0.0),
            (80.0, 0.0),
            (80.0, 30.0),
            (0.0, 30.0),
        ]
    )
    planning_settings = PlanningSettings(
        search_altitude_m=25.0,
        lane_spacing_m=20.0,
    )
    start_reference = make_geo_point(-5.0, 7.5)

    route, metrics = generate_route(
        search_area=search_area,
        planning_settings=planning_settings,
        start_reference=start_reference,
    )

    assert len(route.waypoints) == 4

    # Every MVP search waypoint uses the requested constant search altitude.
    for waypoint in route.waypoints:
        assert waypoint.altitude_m == 25.0
        assert waypoint.altitude_reference == AltitudeReference.HOME_RELATIVE

    assert metrics.distance_to_start_m == pytest.approx(5.0, abs=0.2)
    assert metrics.route_distance_m == pytest.approx(175.0, abs=0.5)


def test_generate_route_reverses_when_other_end_is_closer() -> None:
    search_area = make_search_area(
        [
            (0.0, 0.0),
            (80.0, 0.0),
            (80.0, 30.0),
            (0.0, 30.0),
        ]
    )
    planning_settings = PlanningSettings(
        search_altitude_m=25.0,
        lane_spacing_m=20.0,
    )

    south_route, _ = generate_route(
        search_area=search_area,
        planning_settings=planning_settings,
        start_reference=make_geo_point(-5.0, 7.5),
    )
    north_route, _ = generate_route(
        search_area=search_area,
        planning_settings=planning_settings,
        start_reference=make_geo_point(-5.0, 22.5),
    )

    assert len(south_route.waypoints) == len(north_route.waypoints)

    # Moving the start reference to the other end should reverse the same path.
    for south_waypoint, north_waypoint in zip(
        south_route.waypoints,
        reversed(north_route.waypoints),
    ):
        assert south_waypoint.latitude_deg == pytest.approx(
            north_waypoint.latitude_deg,
            abs=1e-10,
        )
        assert south_waypoint.longitude_deg == pytest.approx(
            north_waypoint.longitude_deg,
            abs=1e-10,
        )


def test_generate_route_keeps_concave_route_inside_after_projection() -> None:
    vertices = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 100.0),
        (70.0, 100.0),
        (70.0, 30.0),
        (30.0, 30.0),
        (30.0, 100.0),
        (0.0, 100.0),
    ]

    search_area = make_search_area(vertices)
    planning_settings = PlanningSettings(
        search_altitude_m=25.0,
        lane_spacing_m=20.0,
    )

    route, _ = generate_route(
        search_area=search_area,
        planning_settings=planning_settings,
        start_reference=make_geo_point(5.0, 5.0),
    )

    # Convert the public lat and lon result back into test meters so the
    # finished route can be checked against the original polygon.
    local_route = [
        TO_LOCAL.transform(
            waypoint.longitude_deg,
            waypoint.latitude_deg,
        )
        for waypoint in route.waypoints
    ]

    # One millimeter allows for the round trip through two local projections.
    assert_route_stays_inside_polygon(
        local_route,
        Polygon(vertices),
        tolerance_m=0.001,
    )


@pytest.mark.parametrize(
    ("planning_settings", "message"),
    [
        (
            PlanningSettings(
                search_altitude_m=25.0,
                lane_spacing_m=0.0,
            ),
            "lane_spacing_m must be greater than zero",
        ),
        (
            PlanningSettings(
                search_altitude_m=25.0,
                lane_spacing_m=-10.0,
            ),
            "lane_spacing_m must be greater than zero",
        ),
        (
            PlanningSettings(
                search_altitude_m=0.0,
                lane_spacing_m=20.0,
            ),
            "search_altitude_m must be greater than zero",
        ),
        (
            PlanningSettings(
                search_altitude_m=-5.0,
                lane_spacing_m=20.0,
            ),
            "search_altitude_m must be greater than zero",
        ),
    ],
)
def test_generate_route_rejects_invalid_planning_settings(
    planning_settings: PlanningSettings,
    message: str,
) -> None:
    search_area = make_search_area(
        [
            (0.0, 0.0),
            (80.0, 0.0),
            (80.0, 40.0),
            (0.0, 40.0),
        ]
    )

    # Zero and negative planning values should fail before route generation.
    with pytest.raises(ValueError, match=message):
        generate_route(
            search_area=search_area,
            planning_settings=planning_settings,
            start_reference=make_geo_point(0.0, 0.0),
        )


def test_connector_rejects_unreachable_endpoint() -> None:
    polygon = Polygon(
        [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (0.0, 10.0),
        ]
    )

    # The endpoint is outside the polygon so no valid inside connector exists.
    with pytest.raises(
        ValueError,
        match="no valid connector exists inside the search polygon",
    ):
        _shortest_path_inside_polygon(
            start=(5.0, 5.0),
            end=(20.0, 5.0),
            polygon=polygon,
        )