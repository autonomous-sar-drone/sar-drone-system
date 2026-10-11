from heapq import heappop, heappush
from math import ceil, hypot

from pyproj import CRS, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry

from app.domain.models.route import PlanningSettings, Route, RouteMetrics, Waypoint
from app.domain.models.search_area import GeoPoint, SearchArea


LocalPoint = tuple[float, float]

GEOMETRY_TOLERANCE_M = 1e-6


def generate_route(
    search_area: SearchArea,
    planning_settings: PlanningSettings,
    start_reference: GeoPoint,
) -> tuple[Route, RouteMetrics]:
    if planning_settings.lane_spacing_m <= 0:
        raise ValueError("lane_spacing_m must be greater than zero")

    if planning_settings.search_altitude_m <= 0:
        raise ValueError("search_altitude_m must be greater than zero")

    # Route math uses local meters instead of latitude and longitude degrees.
    forward, inverse = _build_local_transformers(search_area)

    polygon_points = [
        forward.transform(vertex.longitude_deg, vertex.latitude_deg)
        for vertex in search_area.vertices
    ]
    polygon = Polygon(polygon_points)

    start_reference_xy = forward.transform(
        start_reference.longitude_deg,
        start_reference.latitude_deg,
    )

    local_route = _generate_sweep_route(
        polygon=polygon,
        lane_spacing_m=planning_settings.lane_spacing_m,
    )

    if len(local_route) < 2:
        raise ValueError("search area did not produce a usable route")

    # The route starts from whichever end is closer to the drone.
    distance_to_first = _distance(start_reference_xy, local_route[0])
    distance_to_last = _distance(start_reference_xy, local_route[-1])

    if distance_to_last < distance_to_first:
        local_route.reverse()

    metrics = RouteMetrics(
        distance_to_start_m=_distance(start_reference_xy, local_route[0]),
        route_distance_m=_polyline_length(local_route),
    )

    route = _route_from_local_points(
        local_route,
        inverse,
        planning_settings.search_altitude_m,
    )

    return route, metrics


def _build_local_transformers(
    search_area: SearchArea,
) -> tuple[Transformer, Transformer]:
    center_latitude = (
        sum(vertex.latitude_deg for vertex in search_area.vertices)
        / len(search_area.vertices)
    )
    center_longitude = (
        sum(vertex.longitude_deg for vertex in search_area.vertices)
        / len(search_area.vertices)
    )

    # AEQD gives the mission area a local x and y coordinate system in meters.
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_latitude} +lon_0={center_longitude} "
        "+datum=WGS84 +units=m +no_defs"
    )
    geographic_crs = CRS.from_epsg(4326)

    # PyProj keeps x as longitude and y as latitude with always_xy enabled.
    forward = Transformer.from_crs(geographic_crs, local_crs, always_xy=True)
    inverse = Transformer.from_crs(local_crs, geographic_crs, always_xy=True)

    return forward, inverse


def _generate_sweep_route(
    polygon: Polygon,
    lane_spacing_m: float,
) -> list[LocalPoint]:
    min_x, min_y, max_x, max_y = polygon.bounds
    sweep_y_values = _sweep_y_values(min_y, max_y, lane_spacing_m)

    route: list[LocalPoint] = []
    left_to_right = True

    # Each sweep line is extended far enough to cross the whole polygon.
    sweep_padding = max(max_x - min_x, lane_spacing_m, 1.0)

    for y in sweep_y_values:
        sweep_line = LineString(
            [
                (min_x - sweep_padding, y),
                (max_x + sweep_padding, y),
            ]
        )

        # The intersection keeps only the parts of the sweep inside the polygon.
        clipped = polygon.intersection(sweep_line)
        segments = _extract_line_segments(clipped)

        if not segments:
            continue

        ordered_segments = _order_segments(segments, left_to_right)

        for segment_start, segment_end in ordered_segments:
            if route:
                connector = _shortest_path_inside_polygon(
                    route[-1],
                    segment_start,
                    polygon,
                )

                # The first connector point already exists as the last route point.
                _append_points(route, connector[1:])
            else:
                route.append(segment_start)

            _append_points(route, [segment_end])

        # The next row travels in the opposite direction.
        left_to_right = not left_to_right

    return route


def _sweep_y_values(
    min_y: float,
    max_y: float,
    lane_spacing_m: float,
) -> list[float]:
    height = max_y - min_y

    # The number of rows keeps spacing at or below the requested amount.
    pass_count = max(1, ceil(height / lane_spacing_m))
    actual_spacing = height / pass_count

    # Each sweep sits in the middle of its section of the polygon.
    return [
        min_y + (index + 0.5) * actual_spacing
        for index in range(pass_count)
    ]


def _extract_line_segments(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 0 else []

    if isinstance(geometry, MultiLineString):
        return [segment for segment in geometry.geoms if segment.length > 0]

    if isinstance(geometry, GeometryCollection):
        segments: list[LineString] = []

        # A geometry collection can contain lines along with other geometry types.
        for part in geometry.geoms:
            segments.extend(_extract_line_segments(part))

        return segments

    return []


def _order_segments(
    segments: list[LineString],
    left_to_right: bool,
) -> list[tuple[LocalPoint, LocalPoint]]:
    ordered: list[tuple[LocalPoint, LocalPoint]] = []

    for segment in segments:
        first: LocalPoint = (
            float(segment.coords[0][0]),
            float(segment.coords[0][1]),
        )
        last: LocalPoint = (
            float(segment.coords[-1][0]),
            float(segment.coords[-1][1]),
        )

        # Each segment is oriented in the direction the drone should fly it.
        if left_to_right:
            if first[0] <= last[0]:
                start, end = first, last
            else:
                start, end = last, first
        else:
            if first[0] >= last[0]:
                start, end = first, last
            else:
                start, end = last, first

        ordered.append((start, end))

    # A concave polygon can create several separate segments on one row.
    ordered.sort(
        key=lambda segment: segment[0][0],
        reverse=not left_to_right,
    )

    return ordered


def _shortest_path_inside_polygon(
    start: LocalPoint,
    end: LocalPoint,
    polygon: Polygon,
) -> list[LocalPoint]:
    if start == end:
        return [start]

    # A tiny buffer avoids false outside results from floating point boundary math.
    visibility_polygon = polygon.buffer(GEOMETRY_TOLERANCE_M)

    # The direct path is used when it stays inside the polygon.
    if visibility_polygon.covers(LineString([start, end])):
        return [start, end]

    # The direct path is used when it stays inside the polygon.
    if polygon.covers(LineString([start, end])):
        return [start, end]

    # Shapely repeats the first polygon point at the end of the exterior ring.
    polygon_vertices: list[LocalPoint] = [
        (float(x), float(y))
        for x, y in list(polygon.exterior.coords)[:-1]
    ]

    # Polygon corners are the useful points where a shortest path can bend.
    nodes = _unique_points([start, end, *polygon_vertices])

    distances = {node: float("inf") for node in nodes}
    previous: dict[LocalPoint, LocalPoint] = {}

    distances[start] = 0.0
    queue: list[tuple[float, LocalPoint]] = [(0.0, start)]

    while queue:
        current_distance, current = heappop(queue)

        if current == end:
            break

        # Older heap entries are ignored when a shorter path was found later.
        if current_distance > distances[current]:
            continue

        for candidate in nodes:
            if candidate == current:
                continue

            connection = LineString([current, candidate])

            # covers allows valid paths that travel along the polygon boundary.
            if not visibility_polygon.covers(connection):
                continue

            candidate_distance = current_distance + _distance(current, candidate)

            if candidate_distance < distances[candidate]:
                distances[candidate] = candidate_distance
                previous[candidate] = current
                heappush(queue, (candidate_distance, candidate))

    if distances[end] == float("inf"):
        raise ValueError("no valid connector exists inside the search polygon")

    # The saved previous points rebuild the route from the end back to the start.
    path = [end]

    while path[-1] != start:
        path.append(previous[path[-1]])

    path.reverse()

    return path


def _unique_points(points: list[LocalPoint]) -> list[LocalPoint]:
    unique: list[LocalPoint] = []
    seen: set[LocalPoint] = set()

    for point in points:
        if point not in seen:
            seen.add(point)
            unique.append(point)

    return unique


def _append_points(
    route: list[LocalPoint],
    points: list[LocalPoint],
) -> None:
    for point in points:
        # Duplicate points are not added next to each other in the route.
        if not route or route[-1] != point:
            route.append(point)


def _distance(first: LocalPoint, second: LocalPoint) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]

    return hypot(dx, dy)


def _polyline_length(points: list[LocalPoint]) -> float:
    # Route distance is the sum of each segment between neighboring points.
    return sum(
        _distance(points[index - 1], points[index])
        for index in range(1, len(points))
    )


def _route_from_local_points(
    local_points: list[LocalPoint],
    inverse: Transformer,
    altitude_m: float,
) -> Route:
    waypoints: list[Waypoint] = []

    for x, y in local_points:
        longitude_deg, latitude_deg = inverse.transform(x, y)

        waypoints.append(
            Waypoint(
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                altitude_m=altitude_m,
            )
        )

    return Route(waypoints=tuple(waypoints))