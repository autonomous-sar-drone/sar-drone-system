"""Fixed camera calibration, shared with the Webots world generator by JSON."""
from dataclasses import dataclass
import json
import math
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "camera_geometry.json"


@dataclass(frozen=True, slots=True)
class CameraGeometry:
    source_id: str
    mount_forward_m: float
    mount_left_m: float
    mount_up_m: float
    pitch_down_deg: float
    horizontal_fov_rad: float
    image_width_px: int
    image_height_px: int

    def __post_init__(self):
        if not self.source_id or len(self.source_id) > 128:
            raise ValueError("invalid calibrated camera source")
        for value in (self.mount_forward_m, self.mount_left_m, self.mount_up_m,
                      self.pitch_down_deg, self.horizontal_fov_rad):
            if not math.isfinite(value):
                raise ValueError("nonfinite camera calibration")
        if not 0 < self.pitch_down_deg <= 90:
            raise ValueError("invalid downward mount angle")
        if not 0 < self.horizontal_fov_rad < math.pi:
            raise ValueError("invalid horizontal FOV")
        if not (-2 <= self.mount_left_m <= 2 and -2 <= self.mount_up_m <= 2
                and 0 <= self.mount_forward_m <= 2):
            raise ValueError("camera mount outside supported limits")
        if not (1 <= self.image_width_px <= 4096 and 1 <= self.image_height_px <= 4096):
            raise ValueError("invalid camera pixel dimensions")


def load_camera_geometry(path: Path = DEFAULT_PATH) -> CameraGeometry:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {"sourceId", "mountForwardM", "mountLeftM", "mountUpM",
                "pitchDownDeg", "horizontalFovRad", "imageWidthPx", "imageHeightPx"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("unexpected camera geometry keys")
    for key in expected - {"sourceId", "imageWidthPx", "imageHeightPx"}:
        if type(data[key]) not in (float, int):
            raise ValueError(f"invalid camera setting {key}")
    for key in ("imageWidthPx", "imageHeightPx"):
        if type(data[key]) is not int:
            raise ValueError(f"invalid camera pixel setting {key}")
    if not isinstance(data["sourceId"], str):
        raise ValueError("invalid sourceId")
    return CameraGeometry(
        source_id=data["sourceId"], mount_forward_m=float(data["mountForwardM"]),
        mount_left_m=float(data["mountLeftM"]), mount_up_m=float(data["mountUpM"]),
        pitch_down_deg=float(data["pitchDownDeg"]),
        horizontal_fov_rad=float(data["horizontalFovRad"]),
        image_width_px=data["imageWidthPx"], image_height_px=data["imageHeightPx"],
    )
