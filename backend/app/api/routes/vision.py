"""Camera MJPEG and independent camera/detector health endpoints."""
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas.vision import VisionStatus
from app.vision.vision_runtime import VisionRuntime

router = APIRouter(tags=["vision"])


def _vision(request: Request) -> VisionRuntime:
    return request.app.state.vision_runtime


@router.get("/api/vision/status", response_model=VisionStatus)
async def vision_status(request: Request) -> VisionStatus:
    return _vision(request).status()


async def mjpeg_parts(runtime: VisionRuntime) -> AsyncIterator[bytes]:
    """Each browser uses the same backend latest frame; no per-client image queue."""
    previous_version = -1
    while runtime.camera_state == "LIVE":
        version, jpeg = await runtime.next_stream_frame(previous_version)
        if jpeg is None:
            continue  # next iteration stops if camera source became unavailable
        previous_version = version
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
            + jpeg + b"\r\n"
        )


@router.get("/api/camera/stream.mjpg")
async def camera_stream(request: Request) -> StreamingResponse:
    runtime = _vision(request)
    if runtime.camera_state != "LIVE":
        # A missing source must never look like a live, looping frozen frame.
        raise HTTPException(status_code=503, detail={
            "code": "CAMERA_UNAVAILABLE",
            "message": "Camera source is not currently providing fresh frames.",
            "details": {},
        })
    return StreamingResponse(
        mjpeg_parts(runtime),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "X-Accel-Buffering": "no"},
    )
