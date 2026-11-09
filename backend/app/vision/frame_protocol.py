"""Length-framed JPEG transport used by the Webots adapter.

This protocol is internal to the camera source connection, NOT a public API.
No Webots import is needed to decode it; another camera sender can use it.
"""
import json
import socket
import struct
from typing import Any

from app.vision.camera_frame import CapturedFrame

MAGIC = b"SAR1"
MAX_META_BYTES = 4096
MAX_JPEG_BYTES = 4 * 1024 * 1024
PREFIX = struct.Struct("!4sI")
SIZE = struct.Struct("!I")


class CameraProtocolError(ValueError):
    pass


def encode_packet(frame: CapturedFrame) -> bytes:
    info: dict[str, Any] = {
        "sourceId": frame.source_id,
        "frameId": frame.frame_id,
        "simulationTimeS": frame.simulation_time_s,
        "captureUnixMs": frame.capture_unix_ms,
        "width": frame.width,
        "height": frame.height,
        "horizontalFovRad": frame.horizontal_fov_rad,
    }
    meta = json.dumps(info, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(meta) > MAX_META_BYTES:
        raise ValueError("metadata too large")
    return PREFIX.pack(MAGIC, len(meta)) + meta + SIZE.pack(len(frame.jpeg_bytes)) + frame.jpeg_bytes


def _read_exact(sock: socket.socket, length: int) -> bytes:
    parts: list[bytes] = []
    while length:
        try:
            block = sock.recv(length)
        except socket.timeout as exc:
            if parts:
                raise CameraProtocolError("timed out partway through camera packet") from exc
            raise
        if not block:
            raise ConnectionError("camera sender closed connection")
        parts.append(block)
        length -= len(block)
    return b"".join(parts)


def read_packet(sock: socket.socket) -> CapturedFrame:
    magic, meta_size = PREFIX.unpack(_read_exact(sock, PREFIX.size))
    if magic != MAGIC or not (1 <= meta_size <= MAX_META_BYTES):
        raise CameraProtocolError("invalid camera packet header")
    try:
        meta = json.loads(_read_exact(sock, meta_size))
        if not isinstance(meta, dict) or set(meta) != {
            "sourceId", "frameId", "simulationTimeS", "captureUnixMs",
            "width", "height", "horizontalFovRad"
        }:
            raise ValueError("unexpected camera metadata fields")
        jpeg_size = SIZE.unpack(_read_exact(sock, SIZE.size))[0]
        if not (4 <= jpeg_size <= MAX_JPEG_BYTES):
            raise ValueError("invalid JPEG payload size")
        return CapturedFrame(
            source_id=_require(meta["sourceId"], str),
            frame_id=_require_int(meta["frameId"]),
            simulation_time_s=None if meta["simulationTimeS"] is None else _require_number(meta["simulationTimeS"]),
            capture_unix_ms=_require_int(meta["captureUnixMs"]),
            width=_require_int(meta["width"]),
            height=_require_int(meta["height"]),
            horizontal_fov_rad=None if meta["horizontalFovRad"] is None else _require_number(meta["horizontalFovRad"]),
            jpeg_bytes=_read_exact(sock, jpeg_size),
        )
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise CameraProtocolError("invalid camera packet") from exc


def _require(value: Any, target: type) -> Any:
    if not isinstance(value, target):
        raise ValueError("wrong metadata type")
    return value


def _require_int(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")
    return value


def _require_number(value: Any) -> float:
    if type(value) not in (int, float):
        raise ValueError("expected number")
    return float(value)
