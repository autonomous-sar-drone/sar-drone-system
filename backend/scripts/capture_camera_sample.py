"""Receive a short sample directly from Webots and save a JPEG.

Run when the FastAPI backend is stopped (both would bind the same port).
"""
import argparse
from pathlib import Path
import time

from app.vision.camera_receiver import CameraReceiver


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--output", type=Path, default=Path("camera-sample.jpg"))
    args = parser.parse_args()
    receiver = CameraReceiver(host=args.host, port=args.port)
    receiver.start()
    try:
        deadline = time.monotonic() + args.seconds
        first_id = None
        last_id = None
        while time.monotonic() < deadline:
            frame = receiver.latest()
            if frame is not None:
                if first_id is None:
                    first_id = frame.frame_id
                last_id = frame.frame_id
            time.sleep(0.05)
        frame = receiver.latest()
        if frame is None:
            raise RuntimeError("No frames received. Check Webots console, camera producer and host/port.")
        args.output.write_bytes(frame.jpeg_bytes)
        count, connected, _ = receiver.stats()
        print(f"Received {count} frames, first={first_id}, last={last_id}, "
              f"connected={connected}, dimensions={frame.width}x{frame.height}")
        print(f"Latest frame saved: {args.output.resolve()}")
        if first_id == last_id:
            print("WARNING: only one distinct frame observed; verify simulation is running")
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()
