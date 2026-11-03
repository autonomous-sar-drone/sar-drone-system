from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.detections import router
from app.vision.candidate_store import CandidateStore
from test_candidate_store import evidence


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.candidate_store = CandidateStore("vision-session-fixture")
    yield


def test_empty_collection_and_404():
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/detections").json() == {
            "sessionId": "vision-session-fixture", "revision": 0, "candidates": []
        }
        not_found = client.get("/api/detections/no-such-id/snapshot")
        assert not_found.status_code == 404
        assert not_found.json()["detail"]["code"] == "CANDIDATE_NOT_FOUND"
        not_found = client.post("/api/detections/no-such-id/review", json={"decision": "CONFIRM"})
        assert not_found.status_code == 404
        assert not_found.json()["detail"]["code"] == "CANDIDATE_NOT_FOUND"
        assert client.post("/api/detections/no-such-id/review", json={"decision": "CONFIRMED"}).status_code == 422


def test_publish_review_snapshot_and_reconnect_collection():
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        one = app.state.candidate_store.publish(**evidence())
        two = app.state.candidate_store.publish(**evidence())
        full = client.get("/api/detections").json()
        assert full["revision"] == 2
        assert [entry["candidateNumber"] for entry in full["candidates"]] == [1, 2]
        record = full["candidates"][0]
        assert record["candidateId"] == one.candidate_id
        assert record["status"] == "PENDING"
        assert record["executionId"] == "exec-example"
        assert record["reviewedAt"] is None
        assert record["boundingBox"] == {
            "xPx": 1, "yPx": 1, "widthPx": 3, "heightPx": 5,
            "frameWidthPx": 8, "frameHeightPx": 8,
        }
        assert record["estimatedLocation"]["method"] == "FLAT_GROUND_APPROXIMATION"
        assert record["snapshotUrl"] == f"/api/detections/{one.candidate_id}/snapshot"
        image = client.get(record["snapshotUrl"])
        assert image.headers["content-type"] == "image/jpeg"
        assert image.content == evidence()["snapshot_jpeg"]

        result = client.post(f"/api/detections/{one.candidate_id}/review", json={"decision": "CONFIRM"})
        assert result.status_code == 200
        assert result.json()["revision"] == 3
        assert result.json()["candidate"]["status"] == "CONFIRMED"
        assert result.json()["candidate"]["reviewedAt"] is not None
        again = client.post(f"/api/detections/{one.candidate_id}/review", json={"decision": "CONFIRM"})
        assert again.json() == result.json()
        conflict = client.post(f"/api/detections/{one.candidate_id}/review", json={"decision": "DISMISS"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "CANDIDATE_ALREADY_REVIEWED"
        assert conflict.json()["detail"]["details"]["status"] == "CONFIRMED"
        assert client.get(f"/api/detections/{one.candidate_id}/snapshot").content == image.content
        assert client.get("/api/detections").json()["candidates"][0]["status"] == "CONFIRMED"
        assert client.get("/api/detections").json()["candidates"][1]["candidateId"] == two.candidate_id
