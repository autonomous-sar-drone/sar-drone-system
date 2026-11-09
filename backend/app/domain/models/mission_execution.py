from dataclasses import dataclass, field
from typing import Literal

from app.domain.models.mission_state import MissionState
from app.domain.models.route import Waypoint
from app.domain.models.telemetry import TelemetrySample


InterruptionReason = Literal["CAMERA_UNAVAILABLE", "OPERATOR_STOP"]


@dataclass
class ResumeCheckpoint:
    telemetry: TelemetrySample
    active_target: Waypoint | None


@dataclass
class MissionExecution:
    execution_id: str
    plan_id: str
    state: MissionState

    active_target: Waypoint | None = None
    completed_targets: list[Waypoint] = field(default_factory=list)
    telemetry_history: list[TelemetrySample] = field(default_factory=list)
    resume_checkpoint: ResumeCheckpoint | None = None
    interruption_reason: InterruptionReason | None = None