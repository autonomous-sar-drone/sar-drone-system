"""Independent TCP camera input with a single latest-frame slot.

No Webots or MAVLink dependency. This runs on its own small background thread.
"""
import logging
import socket
import threading
import time

from app.vision.camera_frame import CapturedFrame
from app.vision.frame_protocol import CameraProtocolError, read_packet

LOG = logging.getLogger(__name__)


class CameraReceiver:
    def __init__(self, host: str = "127.0.0.1", port: int = 8766) -> None:
        self.host = host
        self.port = port
        self._latest: CapturedFrame | None = None
        self._updated_at: float | None = None
        self._received = 0
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._server: socket.socket | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("camera receiver is already running")
        # Bind before returning, so misconfigured/busy ports fail at startup.
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(1)
            server.settimeout(0.5)
        except Exception:
            server.close()
            raise
        self.port = server.getsockname()[1]
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-receiver", daemon=True)
        self._thread.start()
        LOG.info("Camera receiver listening on %s:%s", self.host, self.port)

    def _run(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(1.0)
                LOG.info("Camera producer connected from %s", addr)
                with self._lock:
                    self._connected = True
                try:
                    while not self._stop.is_set():
                        try:
                            frame = read_packet(conn)
                        except socket.timeout:
                            continue
                        with self._lock:
                            self._latest = frame
                            self._updated_at = time.monotonic()
                            self._received += 1
                            received = self._received
                        if received == 1 or received % 100 == 0:
                            LOG.info("Camera received frame %s (%sx%s), total=%s", frame.frame_id, frame.width, frame.height, received)
                except (ConnectionError, OSError, CameraProtocolError) as exc:
                    if not self._stop.is_set():
                        LOG.info("Camera producer disconnected: %s", exc)
                finally:
                    with self._lock:
                        self._connected = False
        LOG.info("Camera receiver stopped")

    def latest(self) -> CapturedFrame | None:
        with self._lock:
            return self._latest

    def stats(self) -> tuple[int, bool, float | None]:
        with self._lock:
            return self._received, self._connected, self._updated_at

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                LOG.warning("Camera receiver thread did not exit promptly")
        self._thread = None
        self._server = None
