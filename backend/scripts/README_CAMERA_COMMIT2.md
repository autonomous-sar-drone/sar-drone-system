# Commit 2 — camera MJPEG and independent vision health

The source camera is still the Commit 1 Webots TCP producer on `127.0.0.1:8766`.
Run the FastAPI backend from `backend/` with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` after starting your normal SITL/Webots world. Do **not** run `scripts.capture_camera_sample` simultaneously; it binds the same TCP receiver port.

Endpoints:
- `GET http://127.0.0.1:8000/api/vision/status`: always JSON, separate camera and detector health; the detector correctly says `UNAVAILABLE` until the YOLO commit.
- `GET http://127.0.0.1:8000/api/camera/stream.mjpg`: continuous JPEG multipart stream when fresh camera frames arrive. In a browser, open the URL directly. Returns HTTP 503 when source is unavailable.
- Existing `/ws/events`: sends one `vision_status` snapshot on connecting and sparse new events only on meaningful health transitions. Telemetry and mission events are unchanged.

Important: A successful Webots TCP connection does not make the camera LIVE; only fresh **received** frames do. Camera source becomes UNAVAILABLE after 2.5 seconds of no frames by default. The stream then ends, rather than displaying an old JPEG indefinitely. The `cameraOutageSeconds` counter on HTTP status is informational and may continue increasing without frequent WS broadcasts. Automatic RTL is **not implemented** here; `automaticRtlTriggered` remains false until Commit 7.

Tunable environment variables, before starting the backend:
- `CAMERA_STALE_AFTER_S=2.5`: source-age limit.
- `CAMERA_STARTUP_GRACE_S=3.0`: initial STARTING period if no source arrives.
- `CAMERA_STREAM_MAX_FPS=10.0`: server-side upper bound, independent from capture FPS.
- `CAMERA_STREAM_WIDTH_PX=0`: output width; 0 means native source dimensions. Height scales proportionally.
- `CAMERA_STREAM_JPEG_QUALITY=0`: JPEG quality, 1–95; 0 means no re-encode unless resizing, in which case quality defaults to 85.

At defaults, the stream reuses the already encoded source JPEG without re-encoding; one latest encoded frame is shared by any number of viewing clients. Optional resizing/recompression runs on a worker thread, never the MAVLink navigation task.

Tests: `python -m pytest -q`. No person detection, tracking, annotations, or candidate routes yet. These are part of later commits.
