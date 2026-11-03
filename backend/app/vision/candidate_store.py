"""Session-scoped authoritative candidate history and sparse update fan-out.

Only publish complete, frozen evidence. YOLO/tracking (Commit 4) and evidence
selection/annotation (Commit 5) will call publish() when an encounter qualifies.
"""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from uuid import uuid4

from app.domain.models.detection import (
    DetectionCandidate, EstimatedPersonLocation, PixelBoundingBox,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("candidate timestamps must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CandidateUpdate:
    session_id: str
    revision: int
    candidate: DetectionCandidate


class CandidateNotFound(Exception):
    pass


class CandidateAlreadyReviewed(Exception):
    def __init__(self, candidate: DetectionCandidate) -> None:
        self.candidate = candidate
        super().__init__("candidate already reviewed")


class CandidateStore:
    """One instance per backend process. All mutations occur on its event loop."""

    def __init__(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be blank")
        self.session_id = session_id
        self.revision = 0
        self._next_number = 1
        self._by_id: dict[str, DetectionCandidate] = {}
        self._snapshots: dict[str, bytes] = {}
        self._subscribers: set[asyncio.Queue[CandidateUpdate]] = set()

    def collection(self) -> tuple[int, list[DetectionCandidate]]:
        return self.revision, sorted(self._by_id.values(), key=lambda item: item.candidate_number)

    def get(self, candidate_id: str) -> DetectionCandidate:
        try:
            return self._by_id[candidate_id]
        except KeyError as exc:
            raise CandidateNotFound(candidate_id) from exc

    def snapshot(self, candidate_id: str) -> bytes:
        self.get(candidate_id)
        return self._snapshots[candidate_id]

    def subscribe(self) -> asyncio.Queue[CandidateUpdate]:
        # A slow browser cannot grow memory without bound. Lost revisions cause
        # the frontend's agreed revision-gap refetch rather than silent guessing.
        queue: asyncio.Queue[CandidateUpdate] = asyncio.Queue(maxsize=128)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[CandidateUpdate]) -> None:
        self._subscribers.discard(queue)

    def _publish_update(self, candidate: DetectionCandidate) -> None:
        self.revision += 1
        update = CandidateUpdate(self.session_id, self.revision, candidate)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(update)

    def publish(
        self, *, first_observed_at: datetime, captured_at: datetime,
        confidence: float, bounding_box: PixelBoundingBox,
        snapshot_jpeg: bytes, estimated_location: EstimatedPersonLocation | None,
        execution_id: str | None = None,
    ) -> DetectionCandidate:
        """Called internally after evidence selection; not a public creation API."""
        first = _aware_utc(first_observed_at)
        captured = _aware_utc(captured_at)
        if first > captured:
            raise ValueError("first observation cannot occur after selected capture")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and in [0, 1]")
        if not isinstance(bounding_box, PixelBoundingBox):
            raise TypeError("bounding_box must be a PixelBoundingBox")
        if estimated_location is not None and not isinstance(estimated_location, EstimatedPersonLocation):
            raise TypeError("estimated_location must be an EstimatedPersonLocation or None")
        # The selected annotated image is already prepared by the caller. Reject
        # missing/non-JPEG bytes; never publish an empty/broken snapshot URL.
        if not isinstance(snapshot_jpeg, bytes) or not snapshot_jpeg.startswith(b"\xff\xd8") or not snapshot_jpeg.endswith(b"\xff\xd9"):
            raise ValueError("snapshot must be a complete JPEG byte sequence")

        candidate_id = "c-" + uuid4().hex
        candidate = DetectionCandidate(
            candidate_id=candidate_id, candidate_number=self._next_number,
            execution_id=execution_id, status="PENDING", first_observed_at=first,
            captured_at=captured, published_at=_utc_now(), reviewed_at=None,
            confidence=confidence, bounding_box=bounding_box,
            estimated_location=estimated_location,
            snapshot_url=f"/api/detections/{candidate_id}/snapshot",
        )
        self._next_number += 1
        self._by_id[candidate_id] = candidate
        self._snapshots[candidate_id] = snapshot_jpeg
        self._publish_update(candidate)
        return candidate

    def review(self, candidate_id: str, decision: str) -> DetectionCandidate:
        if decision not in ("CONFIRM", "DISMISS"):
            raise ValueError("unexpected review decision")
        current = self.get(candidate_id)
        desired = "CONFIRMED" if decision == "CONFIRM" else "DISMISSED"
        if current.status == desired:
            return current  # idempotent retry: no timestamp or revision changes
        if current.status != "PENDING":
            raise CandidateAlreadyReviewed(current)
        updated = replace(current, status=desired, reviewed_at=_utc_now())
        self._by_id[candidate_id] = updated
        self._publish_update(updated)
        return updated
