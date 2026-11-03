"""Domain concepts for a frozen, reviewable person-sighting candidate."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CandidateStatus = Literal["PENDING", "CONFIRMED", "DISMISSED"]
LocationMethod = Literal[
    "FLAT_GROUND_APPROXIMATION", "TERRAIN_RAYCAST", "CAMERA_POSITION_FALLBACK",
]
LocationQuality = Literal[
    "APPROXIMATE", "TERRAIN_ADJUSTED", "CAMERA_POSITION_ONLY",
]


@dataclass(frozen=True)
class PixelBoundingBox:
    x_px: int
    y_px: int
    width_px: int
    height_px: int
    frame_width_px: int
    frame_height_px: int

    def __post_init__(self) -> None:
        if (min(self.x_px, self.y_px) < 0 or
            min(self.width_px, self.height_px, self.frame_width_px, self.frame_height_px) <= 0 or
            self.x_px + self.width_px > self.frame_width_px or
            self.y_px + self.height_px > self.frame_height_px):
            raise ValueError("bounding box must be positive and inside the original frame")


@dataclass(frozen=True)
class EstimatedPersonLocation:
    latitude_deg: float
    longitude_deg: float
    method: LocationMethod
    quality: LocationQuality

    def __post_init__(self) -> None:
        import math
        if (not math.isfinite(self.latitude_deg) or not math.isfinite(self.longitude_deg) or
            abs(self.latitude_deg) > 90 or abs(self.longitude_deg) > 180):
            raise ValueError("invalid WGS84 location")
        pairs = {
            "FLAT_GROUND_APPROXIMATION": "APPROXIMATE",
            "TERRAIN_RAYCAST": "TERRAIN_ADJUSTED",
            "CAMERA_POSITION_FALLBACK": "CAMERA_POSITION_ONLY",
        }
        if pairs[self.method] != self.quality:
            raise ValueError("location method and quality disagree")


@dataclass(frozen=True)
class DetectionCandidate:
    candidate_id: str
    candidate_number: int
    execution_id: str | None
    status: CandidateStatus
    first_observed_at: datetime
    captured_at: datetime
    published_at: datetime
    reviewed_at: datetime | None
    confidence: float
    bounding_box: PixelBoundingBox
    estimated_location: EstimatedPersonLocation | None
    snapshot_url: str
