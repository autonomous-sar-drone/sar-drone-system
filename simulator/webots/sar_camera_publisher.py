"""Non-blocking camera publisher for the Webots bridge.

No Webots API calls are performed on the networking/encoding thread. The
simulation thread offers a COPIED BGRA frame; a one-slot queue drops older ones.
Requires Pillow in the Python interpreter selected by Webots.
"""
from io import BytesIO
import json
import queue
import socket
import struct
import threading
import time


class SarCameraPublisher:
    def __init__(self, host, port, source_id="webots-iris", jpeg_quality=80):
        self.host = host
        self.port = port
        self.source_id = source_id
        self.jpeg_quality = jpeg_quality
        self.frames = queue.Queue(maxsize=1)
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="sar-camera-publisher")
        self.thread.start()

    def offer(self, raw_bgra, frame_id, width, height, sim_time_s, horizontal_fov_rad):
        if raw_bgra is None or len(raw_bgra) != width * height * 4:
            return
        # Make the copy before another robot.step() can mutate Webots' buffer.
        capture = (bytes(raw_bgra), frame_id, width, height, sim_time_s,
                   time.time_ns() // 1_000_000, horizontal_fov_rad)
        try:
            self.frames.put_nowait(capture)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(capture)

    @staticmethod
    def encode_capture(capture, source_id, quality):
        from PIL import Image
        raw, frame_id, width, height, sim_time_s, unix_ms, fov_rad = capture
        # Webots publishes BGRA, not RGB; correct channel order is essential.
        rgb = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA").convert("RGB")
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=quality)
        jpeg = output.getvalue()
        meta = json.dumps({
            "sourceId": source_id, "frameId": frame_id,
            "simulationTimeS": sim_time_s, "captureUnixMs": unix_ms,
            "width": width, "height": height, "horizontalFovRad": fov_rad,
        }, separators=(",", ":"), allow_nan=False).encode("utf8")
        return struct.pack("!4sI", b"SAR1", len(meta)) + meta + struct.pack("!I", len(jpeg)) + jpeg

    def _run(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            print("SAR camera disabled: install Pillow in the Webots Python interpreter")
            return
        while not self.done.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=1.5) as connection:
                    connection.settimeout(1.5)
                    print("SAR camera connected to backend %s:%s" % (self.host, self.port))
                    while not self.done.is_set():
                        try:
                            capture = self.frames.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        data = self.encode_capture(capture, self.source_id, self.jpeg_quality)
                        connection.sendall(data)
            except (ConnectionError, OSError) as exc:
                if not self.done.is_set():
                    print("SAR camera waiting for backend: %s" % exc)
                    self.done.wait(1.5)

    def stop(self):
        self.done.set()
        self.thread.join(timeout=2.0)
