from datetime import datetime, timedelta, timezone
import io

import pytest
from PIL import Image

from app.domain.models.detection import EstimatedPersonLocation, PixelBoundingBox
from app.vision.candidate_store import CandidateAlreadyReviewed, CandidateNotFound, CandidateStore


def evidence():
    stamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 10, 30)).save(image, format="JPEG")
    return dict(
        first_observed_at=stamp, captured_at=stamp + timedelta(seconds=1),
        confidence=0.91,
        bounding_box=PixelBoundingBox(1, 1, 3, 5, 8, 8),
        estimated_location=EstimatedPersonLocation(
            30.2674, -97.7432, "FLAT_GROUND_APPROXIMATION", "APPROXIMATE"
        ),
        snapshot_jpeg=image.getvalue(), execution_id="exec-example",
    )


def test_candidates_numbered_and_sorted_and_update_revisions():
    store = CandidateStore("vision-session-test")
    queue = store.subscribe()
    one = store.publish(**evidence())
    two = store.publish(**evidence())
    assert one.candidate_number == 1 and two.candidate_number == 2
    assert one.candidate_id != two.candidate_id
    assert store.collection() == (2, [one, two])
    assert queue.get_nowait().revision == 1
    assert queue.get_nowait().revision == 2
    assert store.snapshot(one.candidate_id) == evidence()["snapshot_jpeg"]
    store.unsubscribe(queue)
    assert not store._subscribers


def test_confirm_repeat_idempotent_conflicting_review_409_path():
    store = CandidateStore("vision-session-test")
    candidate = store.publish(**evidence())
    original_jpeg = store.snapshot(candidate.candidate_id)
    reviewed = store.review(candidate.candidate_id, "CONFIRM")
    assert reviewed.status == "CONFIRMED" and reviewed.reviewed_at is not None
    assert store.revision == 2
    assert store.review(candidate.candidate_id, "CONFIRM") == reviewed
    assert store.revision == 2
    with pytest.raises(CandidateAlreadyReviewed):
        store.review(candidate.candidate_id, "DISMISS")
    assert store.snapshot(candidate.candidate_id) == original_jpeg
    assert store.get(candidate.candidate_id).captured_at == candidate.captured_at


def test_dismiss_and_unknown_candidate():
    store = CandidateStore("s")
    candidate = store.publish(**evidence())
    assert store.review(candidate.candidate_id, "DISMISS").status == "DISMISSED"
    with pytest.raises(CandidateNotFound):
        store.review("missing", "CONFIRM")
    with pytest.raises(CandidateNotFound):
        store.snapshot("missing")


def test_invalid_inputs_do_not_advance_revision():
    store = CandidateStore("s")
    params = evidence()
    params["confidence"] = float("nan")
    with pytest.raises(ValueError):
        store.publish(**params)
    params = evidence()
    params["snapshot_jpeg"] = b"not a jpeg"
    with pytest.raises(ValueError):
        store.publish(**params)
    params = evidence()
    params["first_observed_at"] = params["captured_at"] + timedelta(seconds=1)
    with pytest.raises(ValueError):
        store.publish(**params)
    assert store.collection() == (0, [])
    with pytest.raises(ValueError):
        PixelBoundingBox(7, 1, 3, 3, 8, 8)
    with pytest.raises(ValueError):
        EstimatedPersonLocation(30.0, -97.0, "CAMERA_POSITION_FALLBACK", "APPROXIMATE")


def test_bounded_queue_reports_revision_gap_when_behind():
    store = CandidateStore("s")
    queue = store.subscribe()
    for _ in range(130):
        store.publish(**evidence())
    assert queue.qsize() == 128
    assert queue.get_nowait().revision == 3
    assert store.revision == 130


def test_new_process_has_new_session_without_old_candidates():
    a = CandidateStore("s1")
    a.publish(**evidence())
    b = CandidateStore("s2")
    assert b.collection() == (0, [])
    assert a.collection()[0] == 1
