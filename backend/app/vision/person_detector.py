"""Person-only Ultralytics tracking; optional dependency, kept outside flight code.

One sequential caller must own this instance: the Ultralytics tracker is stateful.
It accepts a CapturedFrame and returns frame-linked observations, not candidates.
"""
from dataclasses import dataclass
from io import BytesIO
import math

from PIL import Image

from app.vision.camera_frame import CapturedFrame


@dataclass(frozen=True, slots=True)
class PersonBox:
    """Pixel coordinates on the original full-resolution source frame."""
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    track_id: int | None

    def __post_init__(self) -> None:
        if min(self.x1, self.y1) < 0 or self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError("invalid person box")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("invalid confidence")
        if self.track_id is not None and self.track_id < 0:
            raise ValueError("invalid tracking ID")


@dataclass(frozen=True, slots=True)
class TrackedFrame:
    """Source-matched results; frame JPEG is intentionally retained for Commit 5."""
    frame: CapturedFrame
    persons: tuple[PersonBox, ...]
    tracking_generation: int = 0  # changes on reset; tracker IDs may be reused


class YoloPersonTracker:
    """A single-thread-owned Ultralytics YOLO + BoT-SORT adapter.

    Importing this module does not import torch, OpenCV or Ultralytics. Start the
    model lazily on a worker thread so camera/mission startup isn't blocked.
    """

    def __init__(
        self, *, model_path: str = "yolo11n.pt", tracker: str = "botsort.yaml",
        confidence: float = 0.25, image_size: int = 640, device: str = "cpu",
    ) -> None:
        if not model_path or not tracker or not device:
            raise ValueError("model, tracker and device must be configured")
        if not math.isfinite(confidence) or not 0 < confidence <= 1:
            raise ValueError("confidence threshold must be in (0, 1]")
        if not 128 <= image_size <= 2048 or image_size % 32:
            raise ValueError("image size must be a multiple of 32 between 128 and 2048")
        self.model_path = model_path
        self.tracker = tracker
        self.confidence = confidence
        self.image_size = image_size
        self.device = device
        self._model = None
        self.tracking_generation = 0

    def initialize(self) -> None:
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)

    def reset(self) -> None:
        """Drop tracker state when the source changes or a long gap occurs."""
        self._model = None
        self.tracking_generation += 1

    def process(self, frame: CapturedFrame) -> TrackedFrame:
        self.initialize()
        # Ultralytics treats numpy images as BGR. Webots sends JPEG; Pillow
        # decodes it as RGB, so reverse the channels for a correct model input.
        import numpy as np

        with Image.open(BytesIO(frame.jpeg_bytes)) as image:
            rgb = image.convert("RGB")
            if rgb.size != (frame.width, frame.height):
                raise ValueError("source JPEG dimensions disagree with metadata")
            bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])

        results = self._model.track(
            bgr, persist=True, tracker=self.tracker, classes=[0],
            conf=self.confidence, imgsz=self.image_size, device=self.device,
            verbose=False,
        )
        if not results:
            return TrackedFrame(frame, (), self.tracking_generation)
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return TrackedFrame(frame, (), self.tracking_generation)

        # Some detections have no assigned track ID yet. Still show them on the
        # live feed, but don't invent IDs or pretend they've been associated.
        coordinates = boxes.xyxy.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        classes = boxes.cls.cpu().tolist()
        track_ids = boxes.id.cpu().tolist() if boxes.id is not None else None
        persons = []
        for index, (coords, score, cls) in enumerate(zip(coordinates, confidences, classes)):
            if int(cls) != 0 or not math.isfinite(float(score)) or not 0 <= float(score) <= 1:
                continue
            if not all(math.isfinite(float(value)) for value in coords):
                continue
            x1 = max(0, min(frame.width - 1, int(round(coords[0]))))
            y1 = max(0, min(frame.height - 1, int(round(coords[1]))))
            x2 = max(0, min(frame.width, int(round(coords[2]))))
            y2 = max(0, min(frame.height, int(round(coords[3]))))
            if x1 >= x2 or y1 >= y2:
                continue
            track_id = int(track_ids[index]) if track_ids is not None else None
            if track_id is not None and track_id < 0:
                track_id = None
            persons.append(PersonBox(x1, y1, x2, y2, float(score), track_id))
        return TrackedFrame(frame, tuple(persons), self.tracking_generation)
