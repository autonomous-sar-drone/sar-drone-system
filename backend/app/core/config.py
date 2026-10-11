import os

MAVLINK_ENDPOINT: str = os.getenv("MAVLINK_ENDPOINT", "tcp:127.0.0.1:5762")

DEFAULT_SEARCH_ALTITUDE_M: float = float(
    os.getenv("DEFAULT_SEARCH_ALTITUDE_M", "25.0")
)
DEFAULT_LANE_SPACING_M: float = float(
    os.getenv("DEFAULT_LANE_SPACING_M", "20.0")
)
MAX_TELEMETRY_AGE_S: float = float(
    os.getenv("MAX_TELEMETRY_AGE_S", "3.0")
)
MAX_TRANSIT_DISTANCE_M: float = float(
    os.getenv("MAX_TRANSIT_DISTANCE_M", "2000.0")
)
MAX_ROUTE_DISTANCE_M: float = float(
    os.getenv("MAX_ROUTE_DISTANCE_M", "10000.0")
)