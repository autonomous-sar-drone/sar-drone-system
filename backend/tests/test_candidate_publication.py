"""Commit 5: real detector observations -> independently reviewable sightings."""
import asyncio
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image
import pytest

from app.vision.camera_frame import CapturedFrame
from app.vision.candidate_publisher import (
    CandidatePublisher, CandidatePublicationWorker, _snapshot_score,
)
from app.vision.candidate_store import CandidateStore
from app.vision.person_detector import PersonBox, TrackedFrame


BASE_MS = 1793700000000


def jpeg(color=(80, 90, 110), size=(640, 360)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def person(x=100, y=80, *, width=50, height=120, score=0.8, track_id=1):
    return PersonBox(x, y, x + width, y + height, score, track_id)


def tracked(num, elapsed_ms, persons=(), *, source="camera-one", generation=0,
            width=640, height=360, image=None):
    frame = CapturedFrame(
        source_id=source, frame_id=num, simulation_time_s=elapsed_ms / 1000,
        capture_unix_ms=BASE_MS + elapsed_ms, width=width, height=height,
        horizontal_fov_rad=1.05,
        jpeg_bytes=image if image is not None else jpeg(size=(width, height)),
    )
    return TrackedFrame(frame, tuple(persons), generation)


def stored(store):
    return store.collection()[1]


@pytest.mark.asyncio
async def test_confirmation_at_one_second_and_no_repeated_publication_for_long_encounter():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    for i in range(45):
        await publisher.process(tracked(i + 1, i * 250, (person(x=100+i//5),)))
        if i < 4:
            assert stored(store) == []
    assert len(stored(store)) == 1
    record = stored(store)[0]
    assert record.candidate_number == 1
    assert record.status == "PENDING"
    assert record.estimated_location is None
    assert record.first_observed_at == datetime.fromtimestamp(BASE_MS / 1000, tz=timezone.utc)
    assert record.execution_id is None
    assert store.revision == 1


@pytest.mark.asyncio
async def test_short_occlusion_and_new_track_id_remain_same_encounter():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    await publisher.process(tracked(1, 0, (person(x=110, track_id=8),)))
    await publisher.process(tracked(2, 1000, (person(x=120, track_id=8),)))
    assert len(stored(store)) == 1
    await publisher.process(tracked(3, 1200, ()))
    await publisher.process(tracked(4, 1400, (person(x=126, track_id=10),)))
    await publisher.process(tracked(5, 1550, (person(x=130, track_id=11),)))
    assert len(stored(store)) == 1
    assert store.revision == 1


@pytest.mark.asyncio
async def test_distinct_later_sighting_gets_new_candidate_and_no_long_term_identity_claim():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    for num, ms in [(1, 0), (2, 1100), (3, 1300), (4, 2000)]:
        await publisher.process(tracked(num, ms, (person(track_id=8),)))
    await publisher.process(tracked(5, 4500, ()))
    await publisher.process(tracked(6, 4800, (person(track_id=8),)))
    await publisher.process(tracked(7, 6000, (person(track_id=8),)))
    assert [c.candidate_number for c in stored(store)] == [1, 2]
    assert stored(store)[1].first_observed_at > stored(store)[0].captured_at


@pytest.mark.asyncio
async def test_two_people_in_same_frame_independent_boxes_snapshots_and_review():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    people = (person(x=100, track_id=1), person(x=440, track_id=2))
    for i, ms in enumerate((0, 400, 1000), start=1):
        await publisher.process(tracked(i, ms, people))
    candidates = stored(store)
    assert len(candidates) == 2
    assert len({c.candidate_id for c in candidates}) == 2
    assert store.revision == 2
    assert {c.bounding_box.x_px for c in candidates} == {100, 440}
    assert candidates[0].captured_at == candidates[1].captured_at
    assert all(c.estimated_location is None for c in candidates)
    assert store.snapshot(candidates[0].candidate_id) != store.snapshot(candidates[1].candidate_id)
    for entry in candidates:
        with Image.open(BytesIO(store.snapshot(entry.candidate_id))) as image:
            assert image.size == (640, 360)
            r, g, b = image.convert("RGB").getpixel((entry.bounding_box.x_px, entry.bounding_box.y_px))
            assert g > r + 30 and g > b + 20  # highlighted target's green rectangle
        meta = store.capture_metadata(entry.candidate_id)
        assert meta is not None and meta.frame_id == 1 and meta.horizontal_fov_rad == 1.05
    confirmed = store.review(candidates[0].candidate_id, "CONFIRM")
    assert confirmed.status == "CONFIRMED"
    assert store.get(candidates[1].candidate_id).status == "PENDING"


@pytest.mark.asyncio
async def test_tracks_dont_merge_two_people_even_after_id_reassignment():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    for n, t in [(1, 0), (2, 300), (3, 650), (4, 1050)]:
        await publisher.process(tracked(n, t, (person(x=100+n*2, track_id=2),
                                              person(x=360-n*2, track_id=3))))
    assert len(stored(store)) == 2
    await publisher.process(tracked(5, 1200, (person(x=113, track_id=44),
                                              person(x=349, track_id=45))))
    await publisher.process(tracked(6, 1400, (person(x=115, track_id=44),
                                              person(x=347, track_id=45))))
    assert len(stored(store)) == 2


@pytest.mark.asyncio
async def test_snapshot_ranking_prefers_visible_interior_frame_and_freezes_forever():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    edge = person(x=100, y=0, score=0.86, track_id=1)
    interior = person(x=103, y=25, score=0.81, track_id=1)
    await publisher.process(tracked(1, 0, (edge,), image=jpeg((160, 5, 5))))
    await publisher.process(tracked(2, 400, (interior,), image=jpeg((5, 150, 5))))
    await publisher.process(tracked(3, 1000, (interior,), image=jpeg((5, 5, 160))))
    record = stored(store)[0]
    assert _snapshot_score(interior, 640, 360) > _snapshot_score(edge, 640, 360)
    assert record.captured_at == datetime.fromtimestamp((BASE_MS + 400) / 1000, tz=timezone.utc)
    assert record.confidence == 0.81
    saved = store.snapshot(record.candidate_id)
    metadata = store.capture_metadata(record.candidate_id)
    assert metadata.frame_id == 2 and metadata.capture_unix_ms == BASE_MS + 400
    with Image.open(BytesIO(saved)) as image:
        r, g, b = image.convert("RGB").getpixel((300, 250))
        assert g > r and g > b  # snapshot came from green original frame
    for i in range(4, 40):
        await publisher.process(tracked(i, 1000+(i-3)*200,
                                        (person(x=104 + min(i-3, 10), y=25 + min(i-3, 10)*4, score=0.99),)))
    store.review(record.candidate_id, "DISMISS")
    assert store.snapshot(record.candidate_id) == saved
    assert store.capture_metadata(record.candidate_id) == metadata
    assert len(stored(store)) == 1


@pytest.mark.asyncio
async def test_no_publication_from_fleeting_detection_or_missing_person():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    await publisher.process(tracked(1, 0, (person(),)))
    for i in range(2, 12):
        await publisher.process(tracked(i, i*250, ()))
    assert stored(store) == []


@pytest.mark.asyncio
async def test_duplicate_frame_or_source_reset_does_not_republish_old_encounter():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    frame = tracked(1, 0, (person(),))
    await publisher.process(frame)
    await publisher.process(frame)
    await publisher.process(tracked(2, 1000, (person(),)))
    assert len(stored(store)) == 1
    await publisher.process(tracked(3, 1200, (person(),), source="second-camera"))
    await publisher.process(tracked(4, 2300, (person(),), source="second-camera"))
    assert len(stored(store)) == 2
    await publisher.process(tracked(5, 2500, (person(),), source="second-camera", generation=1))
    await publisher.process(tracked(6, 3600, (person(),), source="second-camera", generation=1))
    assert len(stored(store)) == 2  # same source, brief tracker reset, position still matches
    assert store.capture_metadata(stored(store)[0].candidate_id).source_id == "camera-one"


@pytest.mark.asyncio
async def test_large_jump_with_new_id_is_not_silently_same_encounter():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    for n, t in ((1, 0), (2, 1200)):
        await publisher.process(tracked(n, t, (person(x=60, track_id=8),)))
    await publisher.process(tracked(3, 1400, (person(x=430, track_id=10),)))
    await publisher.process(tracked(4, 2500, (person(x=430, track_id=10),)))
    assert len(stored(store)) == 2


@pytest.mark.asyncio
async def test_expiration_bounds_ephemeral_memory_and_validation():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store, max_active_encounters=1)
    await publisher.process(tracked(1, 0, (person(x=50), person(x=400))))
    assert len(publisher._active) == 1
    await publisher.process(tracked(2, 3000, ()))
    assert not publisher._active
    for kw in ({"confirmation_s": -1}, {"grace_s": float("nan")},
               {"min_observations": 1}, {"max_active_encounters": 0}):
        with pytest.raises(ValueError):
            CandidatePublisher(store, **kw)


@pytest.mark.asyncio
async def test_publication_failure_retries_without_half_published_record(monkeypatch):
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    import app.vision.candidate_publisher as module
    real = module.annotate_live_frame
    def broken(*args):
        raise RuntimeError("test JPEG failure")
    monkeypatch.setattr(module, "annotate_live_frame", broken)
    await publisher.process(tracked(1, 0, (person(),)))
    with pytest.raises(RuntimeError):
        await publisher.process(tracked(2, 1000, (person(),)))
    assert len(stored(store)) == 0
    monkeypatch.setattr(module, "annotate_live_frame", real)
    await publisher.process(tracked(3, 1100, (person(),)))
    assert len(stored(store)) == 1


class FakeRuntime:
    def __init__(self):
        self.subscribers = []
    def subscribe_detections(self):
        queue = asyncio.Queue(maxsize=16)
        self.subscribers.append(queue)
        return queue
    def unsubscribe_detections(self, queue):
        self.subscribers.remove(queue)


@pytest.mark.asyncio
async def test_worker_subscribes_consumes_and_cleans_up_without_affecting_camera():
    store = CandidateStore("s")
    runtime = FakeRuntime()
    worker = CandidatePublicationWorker(runtime, CandidatePublisher(store))
    worker.start()
    assert len(runtime.subscribers) == 1
    try:
        queue = runtime.subscribers[0]
        queue.put_nowait(tracked(1, 0, (person(),)))
        queue.put_nowait(tracked(2, 1100, (person(),)))
        async def await_candidate():
            while len(stored(store)) < 1:
                await asyncio.sleep(0.005)
        await asyncio.wait_for(await_candidate(), timeout=1.5)
        assert store.revision == 1
    finally:
        await worker.stop()
    assert not runtime.subscribers

@pytest.mark.asyncio
async def test_untracked_boxes_can_be_associated_without_inventing_track_id():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    await publisher.process(tracked(1, 0, (person(track_id=None),)))
    await publisher.process(tracked(2, 1050, (person(x=105, track_id=None),)))
    await publisher.process(tracked(3, 1400, (person(x=109, track_id=20),)))
    assert len(stored(store)) == 1


@pytest.mark.asyncio
async def test_large_original_frame_preserves_pixel_coordinates_and_jpeg_dimensions():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    large_person = person(x=820, y=270, width=160, height=300, score=0.94)
    for num, ms in [(1, 0), (2, 1050)]:
        await publisher.process(tracked(num, ms, (large_person,),
                                        width=1280, height=720))
    selected = stored(store)[0]
    bbox = selected.bounding_box
    assert (bbox.x_px, bbox.y_px, bbox.width_px, bbox.height_px,
            bbox.frame_width_px, bbox.frame_height_px) == (820, 270, 160, 300, 1280, 720)
    with Image.open(BytesIO(store.snapshot(selected.candidate_id))) as image:
        assert image.size == (1280, 720)


def test_source_capture_metadata_rejects_invalid_values_before_publication():
    from app.vision.candidate_store import CandidateCaptureMetadata
    for kwargs in [
        {"source_id": "", "frame_id": 1, "capture_unix_ms": BASE_MS,
         "simulation_time_s": 0, "horizontal_fov_rad": 1},
        {"source_id": "c", "frame_id": 1, "capture_unix_ms": BASE_MS,
         "simulation_time_s": -2, "horizontal_fov_rad": 1},
        {"source_id": "c", "frame_id": 1, "capture_unix_ms": BASE_MS,
         "simulation_time_s": 0, "horizontal_fov_rad": float("nan")},
    ]:
        with pytest.raises(ValueError):
            CandidateCaptureMetadata(**kwargs)
    store = CandidateStore("s")
    metadata = CandidateCaptureMetadata("c", 1, BASE_MS, None, None)
    from test_candidate_store import evidence
    kwargs = evidence()
    kwargs["capture_metadata"] = metadata
    with pytest.raises(ValueError, match="capture metadata disagrees"):
        store.publish(**kwargs)
    assert store.revision == 0


@pytest.mark.asyncio
async def test_tracker_generation_reset_ignores_reused_id_far_away():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    await publisher.process(tracked(1, 0, (person(x=70, track_id=1),)))
    await publisher.process(tracked(2, 1100, (person(x=70, track_id=1),)))
    assert len(stored(store)) == 1
    await publisher.process(tracked(3, 1300, (person(x=410, track_id=1),), generation=1))
    await publisher.process(tracked(4, 2400, (person(x=410, track_id=1),), generation=1))
    assert len(stored(store)) == 2  # reused ID alone does not prove identity


@pytest.mark.asyncio
async def test_short_reset_same_source_matches_spatially_even_with_changed_id():
    store = CandidateStore("s")
    publisher = CandidatePublisher(store)
    await publisher.process(tracked(1, 0, (person(x=70, track_id=1),)))
    await publisher.process(tracked(2, 1100, (person(x=70, track_id=1),)))
    await publisher.process(tracked(3, 1500, (person(x=73, track_id=1),), generation=1))
    await publisher.process(tracked(4, 2000, (person(x=76, track_id=2),), generation=1))
    assert len(stored(store)) == 1
