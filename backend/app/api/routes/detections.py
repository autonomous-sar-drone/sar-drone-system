"""Authoritative process-scoped candidate collection, review and frozen evidence."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api.schemas.detections import (
    DetectionCollectionView, ReviewRequest, ReviewResponse, candidate_view,
)
from app.vision.candidate_store import CandidateAlreadyReviewed, CandidateNotFound, CandidateStore

router = APIRouter(prefix="/api/detections", tags=["detections"])


def _store(request: Request) -> CandidateStore:
    return request.app.state.candidate_store


def _not_found(candidate_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": "CANDIDATE_NOT_FOUND", "message": "Candidate not found in the current backend session.",
        "details": {"candidateId": candidate_id},
    })


@router.get("", response_model=DetectionCollectionView)
async def get_detections(request: Request) -> DetectionCollectionView:
    store = _store(request)
    revision, candidates = store.collection()
    return DetectionCollectionView(session_id=store.session_id, revision=revision,
                                   candidates=[candidate_view(item) for item in candidates])


@router.get("/{candidate_id}/snapshot")
async def get_snapshot(candidate_id: str, request: Request) -> Response:
    try:
        jpeg = _store(request).snapshot(candidate_id)
    except CandidateNotFound as exc:
        raise _not_found(candidate_id) from exc
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/{candidate_id}/review", response_model=ReviewResponse)
async def review_candidate(candidate_id: str, payload: ReviewRequest, request: Request) -> ReviewResponse:
    store = _store(request)
    try:
        candidate = store.review(candidate_id, payload.decision)
    except CandidateNotFound as exc:
        raise _not_found(candidate_id) from exc
    except CandidateAlreadyReviewed as exc:
        raise HTTPException(status_code=409, detail={
            "code": "CANDIDATE_ALREADY_REVIEWED",
            "message": "This candidate has already been reviewed.",
            "details": {"candidateId": candidate_id, "status": exc.candidate.status},
        }) from exc
    return ReviewResponse(session_id=store.session_id, revision=store.revision,
                          candidate=candidate_view(candidate))
