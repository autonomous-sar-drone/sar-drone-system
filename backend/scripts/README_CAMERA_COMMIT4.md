# Camera Commit 4 — person-only YOLO tracking and live overlay

**Scope:** backend only. Uses the frozen Phase 1 `GET /api/camera/stream.mjpg` and
`GET /api/vision/status` / `vision_status` WebSocket shapes unchanged.

## Implementation

- `person_detector.py`: optional Ultralytics `YOLO(...).track(..., persist=True,
  classes=[0])`, default `yolo11n.pt`, `botsort.yaml`; preserve original source
  pixels, timestamp, JPEG, source ID, frame ID, tracker-generation epoch and optional tracker IDs. We use
  BoT-SORT first because Webots' camera moves; it is a tunable starting choice.
- `vision_runtime.py`: sequential detector loop using `asyncio.to_thread` to keep
  inference off the MAVLink/FastAPI event loop; latest source frame replaces old
  frames (no growing inference backlog). Bound observation subscriber queues to
  16 and retain only one latest tracked JPEG in normal operation.
- `overlay.py`: source-resolution boxes and whole-number confidence (`Person 91%`)
  drawn by backend. MJPEG clients share one render; no per-client YOLO work.
  Overlay is retained only for matching camera source and frames newer than
  tracked source image, up to `VISION_OVERLAY_MAX_AGE_S` (default 0.35s). Outside
  this window stream raw video rather than falsely aligned boxes.
- Detector health starts `STARTING`, becomes `LIVE` after a successful inference,
  and `UNAVAILABLE` on model failure or prolonged inference staleness. The camera
  itself can remain `LIVE` even when detector is unavailable. Neither condition
  is an aircraft RTL trigger yet (that is Commit 7).
- No candidate is published in Commit 4; candidate evidence accumulation and
  per-person frozen snapshot selection will be wired in Commit 5. Camera frames
  are not sent to the frontend in JSON, and no frontend or simulator changes are made.

## Windows setup (same backend virtual environment)

In a PowerShell terminal, after installing Commit 4 files:

```powershell
cd C:\Users\helic\Documents\_sem\sarProject\sar-camera-detection\backend
& "C:\Users\helic\Documents\_sem\sarProject\sar-drone-system\.venv\Scripts\python.exe" -m pip install -r requirements-vision.txt
& "C:\Users\helic\Documents\_sem\sarProject\sar-drone-system\.venv\Scripts\python.exe" -m pytest -q
```

Ultralytics downloads the pretrained `yolo11n.pt` weights the first time the
model is used if not already present. `.gitignore` now excludes `*.pt` weights.
The first load may therefore require internet access. Torch + OpenCV are
considerably larger dependencies than the camera-only setup.

For a still-image smoke test without running SITL/Webots (use a captured JPEG
that actually contains a pedestrian):

```powershell
& "C:\Users\helic\Documents\_sem\sarProject\sar-drone-system\.venv\Scripts\python.exe" -m scripts.check_yolo_person --image "C:\path\to\person.jpg" --output "C:\path\to\person_annotated.jpg"
```

Tracking continuity and 3D flight integration **cannot** be established by a
single image. For real integration, launch the known-good WSL SITL, camera-enabled
Webots world and FastAPI server, then inspect the browser MJPEG feed and
`GET /api/vision/status`. The detector should progress `STARTING` → `LIVE` once
inference succeeds. Watch for model download/CPU/GPU setup errors in the FastAPI
log. Run at least a moving-drone, person-in-frame test and a camera-stop test.

## Tunable environment values

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `VISION_DETECTOR_ENABLED` | `true` | Set `false` for raw camera-only mode |
| `VISION_YOLO_MODEL` | `yolo11n.pt` | Path/name of pretrained model |
| `VISION_YOLO_TRACKER` | `botsort.yaml` | Ultralytics tracker, e.g. `bytetrack.yaml` |
| `VISION_YOLO_CONFIDENCE` | `0.25` | Detector cutoff, not calibrated probability |
| `VISION_YOLO_IMAGE_SIZE` | `640` | Inference resolution, multiple of 32 |
| `VISION_YOLO_DEVICE` | `cpu` | CPU initially; `0` for CUDA GPU 0 if compatible |
| `VISION_INFERENCE_MAX_FPS` | `5` | Inference sampling cap; tune after profiling |
| `VISION_OVERLAY_MAX_AGE_S` | `0.35` | Maximum age of source-matched overlay |
| `VISION_DETECTOR_STALE_AFTER_S` | `3.0` | Successful inference recency for LIVE health |

**Limitations:** This is not tested with actual YOLO weights in the build
workspace, only with deterministic fake model/results. Ultralytics model
accuracy, tracking-ID stability, moving-camera overlay drift, GPU selection,
actual FPS and CPU interference must be validated on the user's Windows/Webots
setup. An apparent confidence of 91% is not calibrated 91% probability.

**Licensing:** Ultralytics software and models are offered under AGPL-3.0 or
commercial terms. Our repository currently carries the MIT license. We have
**not silently relicensed** this project. Before merging or distributing a
combined Ultralytics-based application, its licensing obligations must be
resolved deliberately, including model weights and attribution. See
https://www.ultralytics.com/license and https://www.gnu.org/licenses/agpl-3.0.html.
