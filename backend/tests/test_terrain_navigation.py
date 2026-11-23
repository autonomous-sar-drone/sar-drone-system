import math

import pytest

from app.domain.models.route import Route, Waypoint
from app.geospatial.terrain_grid import TerrainGrid
from app.geospatial.terrain_navigation import build_safe_transit, build_terrain_navigation


def terrain_grid():
    elevations = []
    for j in range(61):
        row = []
        for i in range(61):
            north = (i - 30) * 4
            # The ground climbs four meters between 10 m and 20 m north of home.
            row.append(round(min(4, max(0, (north - 10) * 0.4)), 3))
        elevations.append(tuple(row))
    grid = TerrainGrid("unit-test", 30.3678, -97.5396, 166.469,
                       240, 240, 4, tuple(elevations))
    grid.validate()
    return grid


def waypoint(grid, north, west, altitude=18):
    latitude, longitude = grid.local_to_geo(north, west)
    return Waypoint(latitude, longitude, altitude)


def test_hidden_targets_preserve_route_and_anticipate_hill():
    terrain = terrain_grid()
    route = Route((waypoint(terrain, 35, 0), waypoint(terrain, 35, 25)))
    nav = build_terrain_navigation(
        terrain, route, terrain.origin_latitude_deg, terrain.origin_longitude_deg,
        desired_agl_m=18, spacing_m=5,
    )
    assert len(nav) > len(route.waypoints)
    assert [n.visible_index for n in nav if n.completes_visible] == [0, 1]
    assert all(n.target.altitude_m >= 19 + terrain.height_geo(n.target.latitude_deg,
                                                                n.target.longitude_deg)
               for n in nav)
    before_hill = [n.target for n in nav if 0 < terrain.geo_to_local(
        n.target.latitude_deg, n.target.longitude_deg)[0] < 10]
    assert before_hill
    assert any(target.altitude_m > 19 for target in before_hill)
    assert route.waypoints[0].altitude_m == 18


def test_climb_descent_rate_across_turns():
    terrain = terrain_grid()
    route = Route((waypoint(terrain, 35, 0), waypoint(terrain, 5, 0),
                   waypoint(terrain, 35, 30)))
    nav = build_terrain_navigation(
        terrain, route, terrain.origin_latitude_deg, terrain.origin_longitude_deg,
        desired_agl_m=18, spacing_m=4,
    )
    for a, b in zip(nav, nav[1:]):
        n0, w0 = terrain.geo_to_local(a.target.latitude_deg, a.target.longitude_deg)
        n1, w1 = terrain.geo_to_local(b.target.latitude_deg, b.target.longitude_deg)
        segment_m = math.hypot(n1-n0, w1-w0)
        assert abs(a.target.altitude_m - b.target.altitude_m) <= 0.65 / 2.0 * segment_m + 1e-3


def test_planner_rejects_missing_coverage_and_non_home_start():
    terrain = terrain_grid()
    route = Route((waypoint(terrain, 121, 0),))
    with pytest.raises(ValueError, match="outside"):
        build_terrain_navigation(terrain, route, terrain.origin_latitude_deg,
                                 terrain.origin_longitude_deg, 18)
    with pytest.raises(ValueError, match="origin"):
        build_terrain_navigation(terrain, route, *terrain.local_to_geo(20, 0), 18)


def test_transit_checks_full_corridor_and_spacing():
    terrain = terrain_grid()
    start = terrain.local_to_geo(0, 0)
    goal = terrain.local_to_geo(35, 0)
    transit = build_safe_transit(terrain, *start, *goal, 18, spacing_m=3)
    assert len(transit) == math.ceil(35 / 3)
    assert transit[-1].latitude_deg == goal[0]
    assert transit[-1].longitude_deg == goal[1]
    assert all(x.altitude_m >= 19 + terrain.height_geo(x.latitude_deg, x.longitude_deg)
               for x in transit)
    with pytest.raises(ValueError, match="spacing"):
        build_safe_transit(terrain, *start, *goal, 18, spacing_m=0.2)
    with pytest.raises(ValueError, match="outside"):
        build_safe_transit(terrain, *start, *terrain.local_to_geo(121, 0), 18)


def test_step_spacing_changes_internal_count():
    terrain = terrain_grid()
    route = Route((waypoint(terrain, 35, 0),))
    smaller = build_terrain_navigation(
        terrain, route, terrain.origin_latitude_deg, terrain.origin_longitude_deg,
        18, spacing_m=2,
    )
    larger = build_terrain_navigation(
        terrain, route, terrain.origin_latitude_deg, terrain.origin_longitude_deg,
        18, spacing_m=10,
    )
    assert len(smaller) > len(larger)


def test_lookahead_aims_past_intermediate_samples_without_skipping_endpoint():
    from app.geospatial.terrain_navigation import terrain_lookahead_target

    terrain = terrain_grid()
    start = waypoint(terrain, 0, 0, 19)
    path = build_safe_transit(terrain, *terrain.local_to_geo(0, 0),
                              *terrain.local_to_geo(35, 0), 18, spacing_m=5)
    for position in (0, 4, 8, 12, 18, 24, 30):
        lat, lon = terrain.local_to_geo(position, 0)
        guidance = terrain_lookahead_target(terrain, lat, lon, start, path, 8)
        north, west = terrain.geo_to_local(guidance.target.latitude_deg,
                                          guidance.target.longitude_deg)
        assert north == pytest.approx(min(35, position + 8), abs=0.01)
        assert west == pytest.approx(0, abs=0.01)
        assert guidance.cross_track_m == pytest.approx(0, abs=0.001)
        assert guidance.target.altitude_m >= 19
        assert 0 <= guidance.next_index < len(path)
    latitude, longitude = terrain.local_to_geo(40, 0)
    final = terrain_lookahead_target(terrain, latitude, longitude, start, path, 8)
    assert final.target == path[-1]


def test_lookahead_tracks_cross_track_without_cutting_to_next_leg():
    from app.geospatial.terrain_navigation import terrain_lookahead_target

    terrain = terrain_grid()
    start = waypoint(terrain, 0, 0, 19)
    points = build_safe_transit(terrain, *terrain.local_to_geo(0, 0),
                                *terrain.local_to_geo(20, 0), 18, spacing_m=5)
    pos = terrain.local_to_geo(8, 4)
    guidance = terrain_lookahead_target(terrain, *pos, start, points, 8)
    north, west = terrain.geo_to_local(guidance.target.latitude_deg,
                                      guidance.target.longitude_deg)
    assert north == pytest.approx(16, abs=0.01)
    assert west == pytest.approx(0, abs=0.01)
    assert guidance.cross_track_m == pytest.approx(4, abs=0.01)
    with pytest.raises(ValueError, match="lookahead"):
        terrain_lookahead_target(terrain, *pos, start, points, -1)
