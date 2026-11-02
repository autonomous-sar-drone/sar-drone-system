import base64
import socket
import struct
import threading
import time

import pytest

from app.vision.camera_frame import CapturedFrame
from app.vision.camera_receiver import CameraReceiver
from app.vision.frame_protocol import CameraProtocolError, encode_packet, read_packet


# A minimal valid JPEG fixture: backend only stores bytes, it does not decode them.
JPEG_FIXTURE = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDweiiivyI/09P/2Q=='
)


def make_jpeg() -> bytes:
    return JPEG_FIXTURE


def frame(index=1, data=None) -> CapturedFrame:
    return CapturedFrame(
        source_id="webots-iris", frame_id=index, simulation_time_s=index / 10,
        capture_unix_ms=1793581000000 + index, width=8, height=6,
        horizontal_fov_rad=1.1, jpeg_bytes=data or make_jpeg()
    )


def eventually(test, duration=3):
    stop = time.monotonic() + duration
    while time.monotonic() < stop:
        if test():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_packet_roundtrip_fragmented_reads():
    a, b = socket.socketpair()
    original = frame()
    packet = encode_packet(original)

    def send_small_chunks():
        for n in range(0, len(packet), 7):
            a.sendall(packet[n:n+7])
        a.close()

    writer = threading.Thread(target=send_small_chunks)
    writer.start()
    with b:
        parsed = read_packet(b)
    writer.join()
    assert parsed == original


def test_bad_header_and_large_length_rejected_before_allocating():
    for prefix in [b"NOPE" + struct.pack("!I", 10), b"SAR1" + struct.pack("!I", 9999999)]:
        a, b = socket.socketpair()
        with a, b:
            a.sendall(prefix)
            with pytest.raises(CameraProtocolError):
                read_packet(b)


def test_frame_validation_rejects_bad_timestamp_fov_and_jpeg():
    with pytest.raises(ValueError):
        CapturedFrame("x", 1, -1.0, 123, 2, 2, None, make_jpeg())
    with pytest.raises(ValueError):
        CapturedFrame("x", 1, None, 123, 2, 2, 6.3, make_jpeg())
    with pytest.raises(ValueError):
        CapturedFrame("x", 1, 2.0, 123, 2, 2, None, b"not a jpeg")


def test_tcp_receiver_latest_frame_drop_old_and_reconnect():
    receiver = CameraReceiver("127.0.0.1", 0)
    receiver.start()
    try:
        # First producer sends several images without any consumer polling.
        with socket.create_connection(("127.0.0.1", receiver.port), timeout=1) as client:
            for i in range(1, 31):
                client.sendall(encode_packet(frame(i)))
            eventually(lambda: receiver.stats()[0] == 30)
            assert receiver.latest() == frame(30)
            assert receiver.stats()[1] is True
        eventually(lambda: not receiver.stats()[1])

        # A different producer reconnects on the same port after disconnect.
        with socket.create_connection(("127.0.0.1", receiver.port), timeout=1) as client:
            client.sendall(encode_packet(frame(31)))
            eventually(lambda: receiver.stats()[0] == 31)
            assert receiver.latest().frame_id == 31
    finally:
        receiver.stop()


def test_malformed_producer_does_not_kill_listener():
    receiver = CameraReceiver("127.0.0.1", 0)
    receiver.start()
    try:
        with socket.create_connection(("127.0.0.1", receiver.port), timeout=1) as client:
            client.sendall(b"BAD!" + struct.pack("!I", 1))
        with socket.create_connection(("127.0.0.1", receiver.port), timeout=1) as client:
            client.sendall(encode_packet(frame(2)))
            eventually(lambda: receiver.latest() is not None)
            assert receiver.latest().frame_id == 2
    finally:
        receiver.stop()
