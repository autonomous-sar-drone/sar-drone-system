"""Convert source-matched person tracks into one frozen candidate per encounter.

Runs in a single async consumer of VisionRuntime.subscribe_detections().
Track IDs are hints rather than permanent identity; a bounded image-space
association window tolerates brief misses and tracker ID reassignment.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math

from app.domain.models.detection import PixelBoundingBox
from app.vision.camera_frame import CapturedFrame
from app.vision.candidate_store import CandidateCaptureMetadata, CandidateStore
from app.vision.overlay import annotate_live_frame
from app.vision.person_detector import PersonBox, TrackedFrame
from app.vision.person_localization import PersonLocalizer

LOG = logging.getLogger(__name__)


def _source_time(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _box_iou(a: PersonBox, b: PersonBox) -> float:
    overlap_w = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
    overlap_h = max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    intersection = overlap_w * overlap_h
    area_a = (a.x2 - a.x1) * (a.y2 - a.y1)
    area_b = (b.x2 - b.x1) * (b.y2 - b.y1)
    return intersection / (area_a + area_b - intersection)


def _snapshot_score(person: PersonBox, width: int, height: int) -> float:
    """Favor confidence, usable size, full boxes, and comfortable placement.

    The selected frame is from the initial evidence window, never a later
    update to an already published candidate. Vertical centering is a modest
    preference, not a requirement that delays publication indefinitely.
    """
    w, h = person.x2 - person.x1, person.y2 - person.y1
    size = math.sqrt(w * h / (width * height))
    edge_margin = min(
        person.x1 / width, (width - person.x2) / width,
        person.y1 / height, (height - person.y2) / height,
    )
    edge_comfort = min(1.0, max(0.0, edge_margin) / 0.06)
    vertical_midpoint = (person.y1 + person.y2) / (2 * height)
    vertical_comfort = max(0.0, 1.0 - abs(vertical_midpoint - 0.5) * 2)
    return 0.55 * person.confidence + 0.17 * size + 0.20 * edge_comfort + 0.08 * vertical_comfort


@dataclass(slots=True)
class _Encounter:
    encounter_id: int
    first_ms: int
    last_ms: int
    last_box: PersonBox
    last_track_id: int | None
    observations: int
    best_frame: CapturedFrame
    best_box: PersonBox
    best_score: float
    candidate_id: str | None = None


class CandidatePublisher:
    """Stateful, event-loop-owned evidence selector and candidate publisher.

    Limits the active evidence window; it is NOT long-term person recognition.
    A person appearing again after the grace period is a new sighting.
    """

    def __init__(
        self, store: CandidateStore, *, confirmation_s: float = 1.0,
        grace_s: float = 2.0, min_observations: int = 2,
        max_active_encounters: int = 256,
        localizer: PersonLocalizer | None = None,
    ) -> None:
        if not math.isfinite(confirmation_s) or confirmation_s <= 0:
            raise ValueError("confirmation_s must be finite and positive")
        if not math.isfinite(grace_s) or grace_s <= 0:
            raise ValueError("grace_s must be finite and positive")
        if min_observations < 2 or max_active_encounters < 1:
            raise ValueError("invalid encounter limits")
        self.store = store
        self.localizer = localizer
        self.confirmation_ms = round(confirmation_s * 1000)
        self.grace_ms = round(grace_s * 1000)
        self.min_observations = min_observations
        self.max_active_encounters = max_active_encounters
        self._active: dict[int, _Encounter] = {}
        self._next_id = 1
        self._signature: tuple[str, int, int] | None = None
        self._last_tracking_generation: int | None = None
        self._last_frame_id = -1
        self._last_capture_ms = -1

    def _match_score(self, current: PersonBox, previous: _Encounter,
                     frame: CapturedFrame) -> float | None:
        a = current
        b = previous.last_box
        new_w, old_w = a.x2 - a.x1, b.x2 - b.x1
        new_h, old_h = a.y2 - a.y1, b.y2 - b.y1
        # A large scale jump isn't an ordinary continuation, even with same ID.
        if not (0.4 <= new_w / old_w <= 2.5 and 0.4 <= new_h / old_h <= 2.5):
            return None
        delta_s = (frame.capture_unix_ms - previous.last_ms) / 1000
        if delta_s < 0 or delta_s > self.grace_ms / 1000:
            return None
        dx = ((a.x1 + a.x2) - (b.x1 + b.x2)) / (2 * frame.width)
        dy = ((a.y1 + a.y2) - (b.y1 + b.y2)) / (2 * frame.height)
        distance = math.hypot(dx, dy)
        same_id = a.track_id is not None and a.track_id == previous.last_track_id
        # Modest allowance for motion and short missed observations. Same ID
        # helps selection but never overrides clearly implausible movement.
        gate = 0.14 + min(delta_s, 2.0) * 0.07 + (0.04 if same_id else 0)
        if distance > gate:
            return None
        overlap = _box_iou(a, b)
        score = 2.0 * overlap + 1.0 - distance / gate
        if same_id:
            score += 4.0
        elif a.track_id is not None and previous.last_track_id is not None:
            score -= 0.25
        return score

    def _new_encounter(self, person: PersonBox, frame: CapturedFrame) -> _Encounter:
        eid = self._next_id
        self._next_id += 1
        encounter = _Encounter(
            encounter_id=eid, first_ms=frame.capture_unix_ms,
            last_ms=frame.capture_unix_ms, last_box=person,
            last_track_id=person.track_id, observations=1,
            best_frame=frame, best_box=person,
            best_score=_snapshot_score(person, frame.width, frame.height),
        )
        self._active[eid] = encounter
        return encounter

    def _update(self, encounter: _Encounter, person: PersonBox,
                frame: CapturedFrame) -> None:
        encounter.last_ms = frame.capture_unix_ms
        encounter.last_box = person
        encounter.last_track_id = person.track_id
        encounter.observations += 1
        if encounter.candidate_id is not None:
            return  # Frozen snapshot is never replaced after publication.
        score = _snapshot_score(person, frame.width, frame.height)
        if score > encounter.best_score:
            encounter.best_score = score
            encounter.best_frame = frame
            encounter.best_box = person

    def _associate(self, tracked: TrackedFrame) -> list[_Encounter]:
        frame = tracked.frame
        signature = (frame.source_id, frame.width, frame.height)
        if signature != self._signature:
            self._active.clear()
            self._signature = signature
            self._last_tracking_generation = tracked.tracking_generation
            self._last_frame_id = -1
            self._last_capture_ms = -1
        elif tracked.tracking_generation != self._last_tracking_generation:
            # BoT-SORT might reuse numeric IDs after a reset. Continue only on
            # image-space evidence, not on the reused ID; short source-consistent
            # gaps can still be one encounter.
            for encounter in self._active.values():
                encounter.last_track_id = None
            self._last_tracking_generation = tracked.tracking_generation
        # Duplicate or out-of-order results must not accumulate confirmation
        # time or duplicate candidates. A rewound camera starts fresh.
        if frame.frame_id <= self._last_frame_id or frame.capture_unix_ms < self._last_capture_ms:
            if frame.frame_id < self._last_frame_id or frame.capture_unix_ms < self._last_capture_ms:
                self._active.clear()
                self._last_frame_id = -1
                self._last_capture_ms = -1
            else:
                return []
        self._last_frame_id = frame.frame_id
        self._last_capture_ms = frame.capture_unix_ms
        expired = [eid for eid, enc in self._active.items()
                   if frame.capture_unix_ms - enc.last_ms > self.grace_ms]
        for eid in expired:
            del self._active[eid]

        pairs: list[tuple[float, int, int]] = []
        for i, person in enumerate(tracked.persons):
            for eid, encounter in self._active.items():
                score = self._match_score(person, encounter, frame)
                if score is not None:
                    pairs.append((score, i, eid))
        # One-to-one association, including two people in the same source frame.
        pairs.sort(key=lambda item: item[0], reverse=True)
        matched_persons: set[int] = set()
        matched_encounters: set[int] = set()
        observed: list[_Encounter] = []
        for _, i, eid in pairs:
            if i not in matched_persons and eid not in matched_encounters:
                self._update(self._active[eid], tracked.persons[i], frame)
                matched_persons.add(i)
                matched_encounters.add(eid)
                observed.append(self._active[eid])
        for i, person in enumerate(tracked.persons):
            if i not in matched_persons:
                if len(self._active) >= self.max_active_encounters:
                    LOG.warning("Candidate encounter limit reached; skipping extra person observation")
                    continue
                observed.append(self._new_encounter(person, frame))
        return observed

    async def process(self, tracked: TrackedFrame) -> None:
        """Process one inferred source frame and publish qualified sightings."""
        observed = self._associate(tracked)
        for encounter in observed:
            if (encounter.candidate_id is not None or
                encounter.observations < self.min_observations or
                encounter.last_ms - encounter.first_ms < self.confirmation_ms):
                continue
            frame = encounter.best_frame
            box = encounter.best_box
            # Offload encoding so WebSocket, camera receive and flight remain responsive.
            snapshot = await asyncio.to_thread(annotate_live_frame, frame.jpeg_bytes, (box,))
            metadata = CandidateCaptureMetadata(
                source_id=frame.source_id, frame_id=frame.frame_id,
                capture_unix_ms=frame.capture_unix_ms,
                simulation_time_s=frame.simulation_time_s,
                horizontal_fov_rad=frame.horizontal_fov_rad,
            )
            bounding_box = PixelBoundingBox(
                x_px=box.x1, y_px=box.y1,
                width_px=box.x2-box.x1, height_px=box.y2-box.y1,
                frame_width_px=frame.width, frame_height_px=frame.height,
            )
            # Localization is supplemental; a valid sighting must still be
            # published if its pose cannot be projected for any reason.
            try:
                location = self.localizer.estimate(frame, bounding_box) if self.localizer else None
            except Exception:
                LOG.exception("Candidate geolocation failed; publishing the candidate with null location")
                location = None
            candidate = self.store.publish(
                first_observed_at=_source_time(encounter.first_ms),
                captured_at=_source_time(frame.capture_unix_ms),
                confidence=box.confidence,
                bounding_box=bounding_box,
                snapshot_jpeg=snapshot,
                capture_metadata=metadata,
                estimated_location=location,
            )
            encounter.candidate_id = candidate.candidate_id


class CandidatePublicationWorker:
    """Independent bounded consumer; a candidate failure must not stop YOLO."""

    def __init__(self, runtime, publisher: CandidatePublisher) -> None:
        self.runtime = runtime
        self.publisher = publisher
        self._queue = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("candidate worker already started")
        self._queue = self.runtime.subscribe_detections()
        self._task = asyncio.create_task(self._run(), name="vision-candidate-publisher")

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            tracked = await self._queue.get()
            try:
                await self.publisher.process(tracked)
            except Exception:
                LOG.exception("Candidate evidence processing failed; person detection continues")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._queue is not None:
            self.runtime.unsubscribe_detections(self._queue)
            self._queue = None
