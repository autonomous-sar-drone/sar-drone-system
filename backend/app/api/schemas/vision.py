"""Phase 1 camera/detector health contract. Candidate schemas come in Commit 3."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


HealthState = Literal["STARTING", "LIVE", "UNAVAILABLE"]


class CameraHealthView(BaseModel):
    state: HealthState
    last_frame_at: str | None = Field(alias="lastFrameAt")
    model_config = ConfigDict(populate_by_name=True)


class DetectorHealthView(BaseModel):
    state: HealthState
    last_inference_at: str | None = Field(alias="lastInferenceAt")
    model_config = ConfigDict(populate_by_name=True)


class VisionStatus(BaseModel):
    session_id: str = Field(alias="sessionId")
    revision: int = Field(ge=0)
    timestamp: str
    camera: CameraHealthView
    detector: DetectorHealthView
    camera_outage_seconds: float | None = Field(alias="cameraOutageSeconds")
    automatic_rtl_triggered: bool = Field(alias="automaticRtlTriggered")
    model_config = ConfigDict(populate_by_name=True)
