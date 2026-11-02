import importlib.util
from io import BytesIO
from pathlib import Path
import socket
import sys
import time

import pytest

from app.vision.frame_protocol import read_packet

PUBLISHER_FILE = (Path(__file__).parents[2] / "simulator" / "webots" / "sar_camera_publisher.py")
spec = importlib.util.spec_from_file_location("sar_camera_publisher", PUBLISHER_FILE)
assert spec is not None and spec.loader is not None
publisher_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher_module)
SarCameraPublisher = publisher_module.SarCameraPublisher


def test_bgra_red_and_blue_are_not_swapped():
    pytest.importorskip("PIL")
    # Wide patches avoid JPEG chroma-subsampling blending at the edge.
    raw = (bytes([0, 0, 255, 255]) * 16 + bytes([255, 0, 0, 255]) * 16) * 8
    capture = (raw, 5, 32, 8, 11.5, 1793581000000, 1.1)
    packet = SarCameraPublisher.encode_capture(capture, "webots-iris", 100)
    a, b = socket.socketpair()
    with a, b:
        a.sendall(packet)
        received = read_packet(b)
    from PIL import Image
    output = Image.open(BytesIO(received.jpeg_bytes)).convert("RGB")
    red = output.getpixel((4, 4))
    blue = output.getpixel((27, 4))
    assert red[0] > red[2] * 2
    assert blue[2] > blue[0] * 2
    assert received.frame_id == 5
    assert received.simulation_time_s == 11.5


def test_one_frame_queue_never_accumulates_old_captures():
    # Do not establish a backend, so we can confirm publisher input stays bounded.
    p = SarCameraPublisher("127.0.0.1", 0)
    try:
        raw = bytes([0, 0, 255, 255]) * 4
        for i in range(1, 100):
            p.offer(raw, i, 2, 2, i / 10, 1.1)
        assert p.frames.qsize() <= 1
        assert p.frames.queue[-1][1] == 99
    finally:
        p.stop()


def test_actual_publisher_delivers_color_frames_to_generic_receiver():
    from app.vision.camera_receiver import CameraReceiver
    backend = CameraReceiver('127.0.0.1', 0)
    backend.start()
    publisher = SarCameraPublisher('127.0.0.1', backend.port)
    try:
        # 16 by 8 blue BGRA, tagged source time, delivered through real TCP.
        data = bytes([255, 0, 0, 255]) * (16 * 8)
        publisher.offer(data, 17, 16, 8, 2.4, 1.1)
        until = time.monotonic() + 4.0
        while time.monotonic() < until:
            frame = backend.latest()
            if frame is not None:
                break
            time.sleep(0.02)
        assert frame is not None
        assert frame.frame_id == 17
        assert frame.source_id == 'webots-iris'
        assert frame.simulation_time_s == 2.4
        assert frame.width == 16 and frame.height == 8
        from PIL import Image
        rgb = Image.open(BytesIO(frame.jpeg_bytes)).convert('RGB')
        assert rgb.getpixel((5, 5))[2] > 200
    finally:
        publisher.stop()
        backend.stop()
