"""The frozen Phase 1 candidate HTTP and WebSocket wire models."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.detection import DetectionCandidate, EstimatedPersonLocation, PixelBoundingBox
from app.vision.candidate_store import CandidateUpdate


class PixelBoundingBoxView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    x_px: int = Field(alias="xPx")
    y_px: int = Field(alias="yPx")
    width_px: int = Field(alias="widthPx")
    height_px: int = Field(alias="heightPx")
    frame_width_px: int = Field(alias="frameWidthPx")
    frame_height_px: int = Field(alias="frameHeightPx")


class EstimatedPersonLocationView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    latitude_deg: float = Field(alias="latitudeDeg")
    longitude_deg: float = Field(alias="longitudeDeg")
    method: Literal["FLAT_GROUND_APPROXIMATION", "TERRAIN_RAYCAST", "CAMERA_POSITION_FALLBACK"]
    quality: Literal["APPROXIMATE", "TERRAIN_ADJUSTED", "CAMERA_POSITION_ONLY"]


class DetectionCandidateView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str = Field(alias="candidateId")
    candidate_number: int = Field(alias="candidateNumber")
    execution_id: str | None = Field(alias="executionId")
    status: Literal["PENDING", "CONFIRMED", "DISMISSED"]
    first_observed_at: datetime = Field(alias="firstObservedAt")
    captured_at: datetime = Field(alias="capturedAt")
    published_at: datetime = Field(alias="publishedAt")
    reviewed_at: datetime | None = Field(alias="reviewedAt")
    confidence: float = Field(ge=0, le=1)
    bounding_box: PixelBoundingBoxView = Field(alias="boundingBox")
    estimated_location: EstimatedPersonLocationView | None = Field(alias="estimatedLocation")
    snapshot_url: str = Field(alias="snapshotUrl")


class DetectionCollectionView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(alias="sessionId")
    revision: int = Field(ge=0)
    candidates: list[DetectionCandidateView]


class ReviewRequest(BaseModel):
    decision: Literal["CONFIRM", "DISMISS"]


class ReviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(alias="sessionId")
    revision: int = Field(ge=0)
    candidate: DetectionCandidateView


class DetectionUpdateData(ReviewResponse):
    pass


class DetectionUpdateEvent(BaseModel):
    type: Literal["detection_update"] = "detection_update"
    data: DetectionUpdateData


def candidate_view(candidate: DetectionCandidate) -> DetectionCandidateView:
    box: PixelBoundingBox = candidate.bounding_box
    location: EstimatedPersonLocation | None = candidate.estimated_location
    return DetectionCandidateView(
        candidate_id=candidate.candidate_id, candidate_number=candidate.candidate_number,
        execution_id=candidate.execution_id, status=candidate.status,
        first_observed_at=candidate.first_observed_at, captured_at=candidate.captured_at,
        published_at=candidate.published_at, reviewed_at=candidate.reviewed_at,
        confidence=candidate.confidence,
        bounding_box=PixelBoundingBoxView(
            x_px=box.x_px, y_px=box.y_px, width_px=box.width_px, height_px=box.height_px,
            frame_width_px=box.frame_width_px, frame_height_px=box.frame_height_px,
        ),
        estimated_location=EstimatedPersonLocationView(
            latitude_deg=location.latitude_deg, longitude_deg=location.longitude_deg,
            method=location.method, quality=location.quality,
        ) if location is not None else None,
        snapshot_url=candidate.snapshot_url,
    )


def candidate_event(update: CandidateUpdate) -> DetectionUpdateEvent:
    return DetectionUpdateEvent(data=DetectionUpdateData(
        session_id=update.session_id, revision=update.revision,
        candidate=candidate_view(update.candidate),
    ))
