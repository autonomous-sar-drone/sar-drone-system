"""Optional still-image smoke test for the real Ultralytics dependency.

Run with the same backend Python environment used for FastAPI. This is not a
tracking-continuity test (one still image cannot establish stable identities).
"""
import argparse
from pathlib import Path
import time

from PIL import Image

from app.vision.camera_frame import CapturedFrame
from app.vision.overlay import annotate_live_frame
from app.vision.person_detector import YoloPersonTracker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Source JPEG image containing a simulated person")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the annotated JPEG")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    raw = args.image.read_bytes()
    with Image.open(args.image) as original:
        size = original.size
    source = CapturedFrame(
        source_id="offline-test", frame_id=1, simulation_time_s=None,
        capture_unix_ms=int(time.time() * 1000), width=size[0], height=size[1],
        horizontal_fov_rad=None, jpeg_bytes=raw,
    )
    detector = YoloPersonTracker(model_path=args.model, device=args.device)
    found = detector.process(source)
    args.output.write_bytes(annotate_live_frame(raw, found.persons))
    print(f"Found {len(found.persons)} person boxes; annotated image saved at {args.output}")
    for person in found.persons:
        print(f"  bbox=({person.x1},{person.y1})-({person.x2},{person.y2}) ",
              f"confidence={person.confidence:.0%} track_id={person.track_id}")


if __name__ == "__main__":
    main()
