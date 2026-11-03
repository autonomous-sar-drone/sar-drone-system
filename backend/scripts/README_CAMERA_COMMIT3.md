# Commit 3 — Candidate state, review endpoints, event fan-out

This slice implements the Phase 1 contract's **candidate state and review** without adding fake CV findings or changing Webots/ArduPilot.

## Current behavior

- `GET /api/detections` returns `{sessionId, revision, candidates}` including reviewed history, sorted by `candidateNumber`. Before detector/evidence-selection integration it is naturally empty.
- `GET /api/detections/{candidateId}/snapshot` returns the candidate's original, immutable JPEG, or structured 404.
- `POST /api/detections/{candidateId}/review` expects `{ "decision": "CONFIRM" }` or `DISMISS`; it updates status and `reviewedAt`, publishes a candidate update, and returns the authoritative candidate and revision.
- Repeating the same decision returns 200 without revision change. Opposite decision returns structured 409. Invalid decisions return 422.
- Existing `/ws/events` carries `detection_update` with the full candidate; no new WebSocket.
- Candidate store uses the **same process session ID** as vision health, but a separate revision counter. The store is in-memory only and is recreated on backend restart.
- Only complete selected, annotated JPEG evidence can be published. There is **no HTTP create-candidate endpoint**, since candidates are created internally by CV later.

## Intended next integrations

- Commit 4: tracker creates provisional person tracks; **does not** publish a candidate from every raw box.
- Commit 5: evidence selector annotates the chosen source image and calls `app.state.candidate_store.publish(...)` once per qualified encounter. The store creates immutable records and snapshot bytes; reviews never change image or location.
- Commit 6: provide frame-time, independently calculated person geolocation or explicit camera-position fallback to `publish` (currently accepts `estimated_location=None` when defensible position is missing).
- Caller must supply aligned UTC `first_observed_at` and `captured_at`, camera-original `PixelBoundingBox`, JPEG bytes of the selected frame, and confidence between 0 and 1. Unknown detector state remains `UNAVAILABLE` until actual inference code lands.

## Validation

From `backend` with the project virtual environment active:

```powershell
python -m pytest -q
```

With SITL and FastAPI running, `http://127.0.0.1:8000/api/detections` should return an empty collection until the detector integration. Unit and route tests inject **synthetic** candidates directly into the store; no fake detections are emitted in production.

### Note on synchronization

Updates are bounded per connected WebSocket; client bootstrap and reconnect use `GET /api/detections` and apply buffered events newer than the snapshot revision. A gap means the client must refetch, as established in the frontend handoff. No durable replay/history is promised across backend restarts.
