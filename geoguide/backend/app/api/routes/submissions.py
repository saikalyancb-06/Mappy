"""Event submissions: organisers/venues/attendees submit, moderators review, approved events are published."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.events.submissions import SubmissionError, create_submission, is_moderator, my_submissions, review_queue, review_submission
from app.services.profile import user_from_authorization

router = APIRouter(prefix="/api")


def _user(authorization: str | None):
    user = user_from_authorization(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "login_required", "message": "Log in to submit an event."})
    return user


def _moderator(authorization: str | None):
    user = _user(authorization)
    if not is_moderator(user):
        raise HTTPException(status_code=403, detail={"code": "not_a_moderator", "message": "Only moderators can review submissions."})
    return user


def _error(exc: SubmissionError) -> HTTPException:
    status = {"not_found": 404, "duplicate": 409, "already_reviewed": 409, "too_many_pending": 429}.get(exc.code, 422)
    return HTTPException(status_code=status, detail=exc.as_dict())


@router.post("/events/submissions")
def submit_event(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = _user(authorization)
    try:
        saved = create_submission(payload or {}, user)
    except SubmissionError as exc:
        raise _error(exc) from None
    return {"submission": saved, "message": "Thanks! A moderator will check the details before it appears in GeoGuide."}


@router.get("/events/submissions/mine")
def mine(authorization: str | None = Header(default=None)) -> dict:
    user = _user(authorization)
    return {"items": my_submissions(user), "moderator": is_moderator(user)}


@router.get("/events/submissions/review")
def queue(status: str = Query("pending", pattern="^(pending|approved|rejected)$"), authorization: str | None = Header(default=None)) -> dict:
    _moderator(authorization)
    return {"items": review_queue(status)}


@router.post("/events/submissions/{submission_id}/review")
def review(submission_id: str, payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    moderator = _moderator(authorization)
    safe = payload or {}
    try:
        return {"submission": review_submission(submission_id, str(safe.get("decision") or ""), moderator, safe.get("note"))}
    except SubmissionError as exc:
        raise _error(exc) from None
