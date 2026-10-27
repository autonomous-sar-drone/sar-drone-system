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
TARGET_REACHED_TOLERANCE_M: float = float(
    os.getenv("TARGET_REACHED_TOLERANCE_M", "3.0")
)
TARGET_ALTITUDE_TOLERANCE_M: float = float(
    os.getenv("TARGET_ALTITUDE_TOLERANCE_M", "2.0")
)
TAKEOFF_ALTITUDE_TOLERANCE_M: float = float(
    os.getenv("TAKEOFF_ALTITUDE_TOLERANCE_M", "1.5")
)
TAKEOFF_TIMEOUT_S: float = float(
    os.getenv("TAKEOFF_TIMEOUT_S", "30.0")
)
LANDING_ALTITUDE_TOLERANCE_M: float = float(
    os.getenv("LANDING_ALTITUDE_TOLERANCE_M", "0.75")
)
LANDING_SPEED_TOLERANCE_MPS: float = float(
    os.getenv("LANDING_SPEED_TOLERANCE_MPS", "0.75")
)