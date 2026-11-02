# Capture probe: why this is standalone

`python -m scripts.capture_camera_sample` binds the same camera input port as
FastAPI. Do not run it simultaneously with the backend. It is a temporary
on-disk diagnostic to prove that the simulator sends full-color JPEG frames;
no public debugging API was added to the frozen Phase 1 contract.

The normal backend's camera receiver only keeps its latest `CapturedFrame` in
memory and prints sparse diagnostic logs. Live MJPEG output is planned for
Commit 2. Capture source time and aircraft pose association are future work;
a frame's `captureUnixMs` is an approximate wall-clock timestamp, not a
verified cross-machine synchronized MAVLink timestamp.
