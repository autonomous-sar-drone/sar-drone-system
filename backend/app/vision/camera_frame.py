"""A single capture from any camera source, independent of Webots."""
from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    source_id: str
    frame_id: int
    simulation_time_s: float | None
    capture_unix_ms: int
    width: int
    height: int
    horizontal_fov_rad: float | None
    jpeg_bytes: bytes

    def __post_init__(self) -> None:
        if not self.source_id or len(self.source_id) > 128:
            raise ValueError("invalid camera source id")
        if self.frame_id < 0 or self.capture_unix_ms <= 0:
            raise ValueError("invalid frame id or capture timestamp")
        if not (1 <= self.width <= 4096 and 1 <= self.height <= 4096):
            raise ValueError("invalid frame dimensions")
        if self.simulation_time_s is not None and (
            not math.isfinite(self.simulation_time_s) or self.simulation_time_s < 0
        ):
            raise ValueError("invalid simulation timestamp")
        if self.horizontal_fov_rad is not None and (
            not math.isfinite(self.horizontal_fov_rad)
            or not (0.0 < self.horizontal_fov_rad < math.pi)
        ):
            raise ValueError("invalid horizontal field of view")
        if not (4 <= len(self.jpeg_bytes) <= 4 * 1024 * 1024):
            raise ValueError("invalid JPEG length")
        if not self.jpeg_bytes.startswith(b"\xff\xd8") or not self.jpeg_bytes.endswith(b"\xff\xd9"):
            raise ValueError("camera payload must be a JPEG")
