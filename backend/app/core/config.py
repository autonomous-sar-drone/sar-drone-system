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
# camera producer connects to this independent TCP listener, not MAVLink
CAMERA_RECEIVER_HOST: str = os.getenv("CAMERA_RECEIVER_HOST", "127.0.0.1")
CAMERA_RECEIVER_PORT: int = int(os.getenv("CAMERA_RECEIVER_PORT", "8766"))

# Health uses backend frame arrival, not browser stream connectivity.
CAMERA_STALE_AFTER_S: float = float(os.getenv("CAMERA_STALE_AFTER_S", "2.5"))
CAMERA_STARTUP_GRACE_S: float = float(os.getenv("CAMERA_STARTUP_GRACE_S", "3.0"))
CAMERA_RTL_OUTAGE_S: float = float(os.getenv("CAMERA_RTL_OUTAGE_S", "5.0"))
CAMERA_WATCHDOG_POLL_S: float = float(os.getenv("CAMERA_WATCHDOG_POLL_S", "0.2"))
CAMERA_STREAM_MAX_FPS: float = float(os.getenv("CAMERA_STREAM_MAX_FPS", "10.0"))
CAMERA_STREAM_WIDTH_PX: int = int(os.getenv("CAMERA_STREAM_WIDTH_PX", "0"))  # 0 = source resolution
CAMERA_STREAM_JPEG_QUALITY: int = int(os.getenv("CAMERA_STREAM_JPEG_QUALITY", "0"))  # 0 = passthrough

# Optional YOLO person-only tracker; inference runs off the mission event loop.
# Missing ultralytics/weights must not prevent telemetry or raw MJPEG streaming.
VISION_DETECTOR_ENABLED: bool = os.getenv("VISION_DETECTOR_ENABLED", "true").lower() in ("1", "true", "yes")
VISION_YOLO_MODEL: str = os.getenv("VISION_YOLO_MODEL", "yolo11n.pt")
VISION_YOLO_TRACKER: str = os.getenv("VISION_YOLO_TRACKER", "botsort.yaml")
VISION_YOLO_CONFIDENCE: float = float(os.getenv("VISION_YOLO_CONFIDENCE", "0.25"))
VISION_YOLO_IMAGE_SIZE: int = int(os.getenv("VISION_YOLO_IMAGE_SIZE", "640"))
VISION_YOLO_DEVICE: str = os.getenv("VISION_YOLO_DEVICE", "cpu")
VISION_INFERENCE_MAX_FPS: float = float(os.getenv("VISION_INFERENCE_MAX_FPS", "5"))
VISION_OVERLAY_MAX_AGE_S: float = float(os.getenv("VISION_OVERLAY_MAX_AGE_S", "0.35"))
VISION_DETECTOR_STALE_AFTER_S: float = float(os.getenv("VISION_DETECTOR_STALE_AFTER_S", "3.0"))

# In-memory sightings: confirm after approximately one second of associated
# evidence; tolerate short tracker-ID changes/occlusions without duplicate cards.
VISION_CANDIDATE_CONFIRMATION_S: float = float(os.getenv("VISION_CANDIDATE_CONFIRMATION_S", "1.0"))
VISION_CANDIDATE_GRACE_S: float = float(os.getenv("VISION_CANDIDATE_GRACE_S", "2.0"))
VISION_CANDIDATE_MIN_OBSERVATIONS: int = int(os.getenv("VISION_CANDIDATE_MIN_OBSERVATIONS", "2"))
