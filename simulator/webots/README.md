# Commit 1 — Webots color camera adapter

Our SAR repo is MIT-licensed, while the upstream ArduPilot repository uses
GPLv3. We intentionally **do not vendor full ArduPilot controllers or world
files** in this repo. Instead, this directory versions two small controller
patches, a camera-world generator, a separate camera publisher owned by our
project, and a guarded installer. It modifies the installed ArduPilot checkout
on the user's machine after verifying the originals and taking backups.

## What is installed

- A **new** `worlds/iris_wsl_camera.wbt`, generated from the existing
  `iris_wsl.wbt`, with the same physics, 2 ms step, motor names, controller,
  original WSL SITL IP and viewpoint, plus a fixed forward/downward RGB
  `sar_camera` in the Iris `extensionSlot`. Camera translation is `0.32 0 0.05` m (forward of the Iris body), with the previously agreed fixed forward/downward tilt.
- Small changes to the existing ArduPilot `webots_vehicle.py` and
  `ardupilot_vehicle_controller.py` to forward image frames, leaving the
  existing grayscale sample code, flight simulation and sensor loop intact.
- One new `sar_camera_publisher.py`, sending JPEG to Windows FastAPI on
  `127.0.0.1:8766` using a one-slot latest-frame queue. It copies the Webots
  BGRA data after the simulation step and encodes on a background thread.
- No changes to `SIM_Webots_Python.cpp/.h`, the Iris PROTO, the original
  `iris_wsl.wbt`, parameter files, MAVLink reader, or flight-control code.

## Install (WSL Ubuntu, NOT PowerShell)

With Webots and SITL stopped, change to the new Git project's directory as
visible from WSL:

```bash
cd /mnt/c/Users/helic/Documents/_sem/sarProject/sar-camera-detection
python3 simulator/webots/install.py --ardupilot ~/ardupilot-4.7.0 --check
```

The check requires the uploaded upstream controllers to match known hashes.
The original world may vary **only** by its `--sitl-address`, which the
installer preserves. If it reports `BLOCKED`, **do not force the patch**;
we need to compare your live version with the archived source first.

If the check passes:

```bash
python3 simulator/webots/install.py --ardupilot ~/ardupilot-4.7.0 --apply
```

The installer applies the stored patches to temporary copies first and
verifies the resulting hashes. Existing controller files are backed up in
`~/ardupilot-4.7.0/sar-camera-source-backup/<UTC timestamp>/` before changes.
The original world remains available for rollback by switching back to
`iris_wsl.wbt` (full controller rollback uses these backups).

Open **`iris_wsl_camera.wbt`**, *not* the original world, in Webots:

`\\wsl.localhost\Ubuntu\home\luis\ardupilot-4.7.0\libraries\SITL\examples\Webots_Python\worlds\iris_wsl_camera.wbt`

## Webots Python interpreter and networking

Webots runs on Windows in your setup, so its Python controller uses the Python
interpreter configured under Webots **Tools → Preferences → Python command**.
That interpreter needs **Pillow**, used to encode RGB JPEG. Check with
`python -c "from PIL import Image"` in that interpreter and install Pillow
there if missing. If absent, the controller prints a camera-disabled warning,
leaving the flight bridge operational.

The backend in your previous integration tests also ran on Windows, making
`127.0.0.1:8766` the simplest camera connection. If the backend moves to WSL
or another machine, change the camera destination host in the generated
world's `controllerArgs` and configure `CAMERA_RECEIVER_HOST/PORT` in FastAPI.
Do not expose the raw local socket to untrusted networks; it is not encrypted
or authenticated.

The original world contains an environment-specific WSL `--sitl-address`. The
installer copies **the current original world's** address into the camera
world. Confirm it is appropriate for your current WSL networking. It is
**not** the same as the camera destination host.

## Run the camera test, without YOLO or the browser

1. Start known-good SITL with its existing parameter-file order and open
   `iris_wsl_camera.wbt`. Do **not** tune ArduPilot parameters as part of
   camera Commit 1.
2. Stop FastAPI for the first test so port 8766 is free.
3. In the Windows project's backend `.venv`:

   ```powershell
   cd C:\Users\helic\Documents\_sem\sarProject\sar-camera-detection\backend
   python -m scripts.capture_camera_sample --seconds 15 --output "$env:TEMP\sar-camera-test.jpg"
   ```

4. The probe reports frame count, IDs, dimensions and current connection. Open
   the saved JPEG and verify color, downward/forward view and changes as the
   drone moves. The Webots camera is **640×360**, initially **10 FPS**, JPEG
   quality **80**, horizontal FOV **1.10 rad**, with a **0.70 rad positive Y
   rotation** (roughly 40° below local +X). This geometry is provisional and
   must be checked visually; do not assume its geolocation transform verified.
5. Stop the probe, then run FastAPI. It should log `Camera receiver listening`,
   `Camera producer connected` and `Camera received frame ...`. The backend
   keeps the newest `CapturedFrame` in `app.state.camera_receiver`; there is
   intentionally **no public camera-stream HTTP endpoint yet** (Commit 2).
6. Fly a brief known-good route, watch telemetry/controller responsiveness,
   and perform a clean RTL. If flight instability appears, preserve logs and
   stop before committing. This test cannot be performed in the packaging
   environment.

## What the frames contain

The source-independent backend frame has `sourceId`, `frameId`, original JPEG,
resolution, Webots `simulationTimeS`, approximate `captureUnixMs`, and
`horizontalFovRad`. The fixed camera translation/rotation are versioned in
the world-generation script. Captured wall-clock time does **not** yet prove
alignment with the vehicle's MAVLink pose samples. Commit 6 will address
pose matching and per-person ground projection; the later terrain slice can
supply terrain elevation without changing the camera transport.

## Intentional limits

No MJPEG viewer, YOLO, tracker, candidates, geolocation, Phase 2 manual controls,
terrain generation or camera-loss auto RTL in Commit 1. A real-drone camera
source is not implemented. Later camera failure handling must use the
backend-source 5-second watchdog, not a browser-only connection issue.
