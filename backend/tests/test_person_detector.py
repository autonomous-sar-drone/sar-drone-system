from io import BytesIO

from PIL import Image
import pytest

from app.vision.camera_frame import CapturedFrame
from app.vision.overlay import annotate_live_frame
from app.vision.person_detector import PersonBox, TrackedFrame, YoloPersonTracker


def frame(source="camera-A", frame_id=7, stamp=1793500000000):
    out = BytesIO()
    Image.new("RGB", (32, 24), (205, 22, 15)).save(out, format="JPEG")
    return CapturedFrame(source, frame_id, 1.0, stamp, 32, 24, 1.0, out.getvalue())


class TensorLike:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class FakeBoxes:
    def __init__(self, coords, confidence, classes, ids):
        self.xyxy = TensorLike(coords)
        self.conf = TensorLike(confidence)
        self.cls = TensorLike(classes)
        self.id = TensorLike(ids) if ids is not None else None

    def __len__(self):
        return len(self.conf.values)


class FakeModel:
    def __init__(self, boxes):
        self.boxes = boxes
        self.kwargs = None
        self.image = None

    def track(self, image, **kwargs):
        self.kwargs = kwargs
        self.image = image
        return [type("Result", (), {"boxes": self.boxes})()]


def test_person_only_tracks_original_pixels_and_converts_bgr():
    model = FakeModel(FakeBoxes(
        [[1.1, 2.2, 20.6, 22.1], [5, 3, 15, 17], [-5, 0, 40, 24]],
        [0.93, 0.99, 0.8], [0, 2, 0], [9, 10, 11],
    ))
    detector = YoloPersonTracker()
    detector._model = model  # no weight download or network needed
    result = detector.process(frame())
    assert result.frame.frame_id == 7
    assert len(result.persons) == 2  # 'car' class omitted even if returned
    assert result.persons[0] == PersonBox(1, 2, 21, 22, 0.93, 9)
    assert result.persons[1] == PersonBox(0, 0, 32, 24, 0.8, 11)
    assert result.tracking_generation == 0
    detector.reset()
    assert detector.tracking_generation == 1  # prevents tracker-ID reuse across reconnections
    assert model.kwargs["classes"] == [0]
    assert model.kwargs["persist"] is True
    assert model.kwargs["tracker"] == "botsort.yaml"
    # On input, BGR means red-dominant source has blue at index zero.
    assert int(model.image[0, 0, 2]) > int(model.image[0, 0, 0])


def test_untracked_persons_still_have_boxes_without_fake_track_ids():
    model = FakeModel(FakeBoxes([[1, 1, 12, 18]], [0.52], [0], None))
    detector = YoloPersonTracker()
    detector._model = model
    assert detector.process(frame()).persons[0].track_id is None


def test_empty_results_and_bad_box_do_not_publish_false_objects():
    detector = YoloPersonTracker()
    detector._model = FakeModel(FakeBoxes([[5, 2, 5, 4], [1, 2, 3, 7]], [0.8, 0.5], [0, 3], None))
    assert detector.process(frame()).persons == ()
    with pytest.raises(ValueError):
        YoloPersonTracker(image_size=641)
    with pytest.raises(ValueError):
        PersonBox(0, 0, 9, 10, float("nan"), None)


def test_overlay_annotation_is_valid_and_no_person_passthrough():
    original = frame().jpeg_bytes
    boxes = (PersonBox(1, 2, 25, 22, 0.91, 3),)
    rendered = annotate_live_frame(original, boxes)
    assert rendered != original
    assert annotate_live_frame(original, ()) is original
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (32, 24)
        assert image.mode == "RGB"


def test_inconsistent_source_dimensions_refused():
    model = FakeModel(FakeBoxes([], [], [], None))
    detector = YoloPersonTracker()
    detector._model = model
    bad = frame()
    from dataclasses import replace
    with pytest.raises(ValueError, match="dimensions"):
        detector.process(replace(bad, width=60))
