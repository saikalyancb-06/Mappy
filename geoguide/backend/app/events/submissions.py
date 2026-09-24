"""Event submissions from organisers, venues and attendees, published only after review.

This is how events that no API lists (temple festivals, community meet-ups, small
venues) reach GeoGuide. A submission is validated here, reviewed by a moderator
(``EVENT_MODERATOR_EMAILS``), and on approval becomes a stored event with the source
"<organiser> (organiser submission, reviewed)". The contact email is never published.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.config import EVENT_MODERATOR_EMAILS
from app.core.rules import load_rules
from app.core.text import name_similarity, normalize
from app.db.models import Destination, EventFestival, EventSubmission, User
from app.db.session import SessionLocal
from app.geo.distance import haversine_km, valid_coordinates
from app.geo.geocoding import city_extent_km

ROLES = {"organiser", "venue", "attendee"}
PRICE_KINDS = {"free", "paid", "donation", "unknown"}
MAX_PENDING_PER_USER = 10
MAX_SPAN_DAYS = 60
MAX_AHEAD_DAYS = 400
DUPLICATE_TITLE_SIMILARITY = 0.85
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SubmissionError(ValueError):
    def __init__(self, field: str, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.field, self.message, self.code = field, message, code

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message, "code": self.code}


def is_moderator(user: User | None) -> bool:
    return bool(user and user.email and user.email.lower() in EVENT_MODERATOR_EMAILS)


def _text(payload: dict[str, Any], key: str, *, required: bool = False, min_len: int = 0, max_len: int = 200) -> str | None:
    value = " ".join(str(payload.get(key) or "").split())
    if not value:
        if required:
            raise SubmissionError(key, "This field is required.")
        return None
    if len(value) < min_len or len(value) > max_len:
        raise SubmissionError(key, f"Use between {min_len} and {max_len} characters.")
    return value


def _url(payload: dict[str, Any], key: str) -> str | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    if len(value) > 500 or not re.match(r"^https?://[^\s/$.?#][^\s]*\.[^\s]{2,}", value, re.IGNORECASE):
        raise SubmissionError(key, "Use a full web address starting with http:// or https://.")
    return value


def _day(payload: dict[str, Any], key: str) -> date | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise SubmissionError(key, "Use a date like 2026-10-20.") from None


def _time(payload: dict[str, Any], key: str) -> str | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    if not _TIME.match(value):
        raise SubmissionError(key, "Use a 24-hour time like 18:30.")
    return value


@dataclass
class SubmissionInput:
    values: dict[str, Any]
    destination: Destination


def parse_submission(payload: dict[str, Any], today: date | None = None) -> SubmissionInput:
    """Validate a submission. Raises SubmissionError naming the first bad field."""
    today = today or datetime.now(timezone.utc).date()
    values: dict[str, Any] = {}
    role = str(payload.get("role") or "").strip().lower()
    if role not in ROLES:
        raise SubmissionError("role", "Say whether you are the organiser, the venue or an attendee.")
    values["role"] = role
    values["title"] = _text(payload, "title", required=True, min_len=3, max_len=120)
    values["description"] = _text(payload, "description", max_len=2000)
    values["organiser_name"] = _text(payload, "organiser_name", required=True, min_len=2, max_len=120)
    email = str(payload.get("contact_email") or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 200:
        raise SubmissionError("contact_email", "Give an email address a moderator can use to confirm the details.")
    values["contact_email"] = email
    category = str(payload.get("category") or "").strip().lower()
    if category not in load_rules("events")["categories"]:
        raise SubmissionError("category", "Choose a category from the list.")
    values["category"] = category

    start = _day(payload, "start_date")
    if start is None:
        raise SubmissionError("start_date", "This field is required.")
    end = _day(payload, "end_date") or start
    if end < start:
        raise SubmissionError("end_date", "The end date is before the start date.")
    if (end - start).days > MAX_SPAN_DAYS:
        raise SubmissionError("end_date", f"Events longer than {MAX_SPAN_DAYS} days can't be submitted here.")
    if end < today - timedelta(days=1):
        raise SubmissionError("end_date", "This event has already ended.")
    if start > today + timedelta(days=MAX_AHEAD_DAYS):
        raise SubmissionError("start_date", "Submit events up to about a year ahead.")
    values["start_date"], values["end_date"] = start.isoformat(), end.isoformat()
    values["start_time"], values["end_time"] = _time(payload, "start_time"), _time(payload, "end_time")
    if values["end_time"] and not values["start_time"]:
        raise SubmissionError("start_time", "Give a start time as well as an end time.")
    if start == end and values["start_time"] and values["end_time"] and values["end_time"] <= values["start_time"]:
        raise SubmissionError("end_time", "The end time is before the start time.")

    destination_id = str(payload.get("destination_id") or "").strip()
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id) if destination_id else None
    if destination is None:
        raise SubmissionError("destination_id", "Choose the city the event takes place in.")
    values["destination_id"] = destination.id
    values["venue_name"] = _text(payload, "venue_name", required=True, min_len=2, max_len=160)
    values["venue_address"] = _text(payload, "venue_address", max_len=300)
    lat, lon = payload.get("venue_lat"), payload.get("venue_lon")
    if lat not in (None, "") or lon not in (None, ""):
        if not valid_coordinates(lat, lon):
            raise SubmissionError("venue_lat", "The venue coordinates are not valid.")
        if haversine_km(float(lat), float(lon), destination.lat, destination.lon) > city_extent_km(destination) + 10:
            raise SubmissionError("venue_lat", f"The venue is not in or near {destination.name}.")
        values["venue_lat"], values["venue_lon"] = float(lat), float(lon)

    price_kind = str(payload.get("price_kind") or "unknown").strip().lower()
    if price_kind not in PRICE_KINDS:
        raise SubmissionError("price_kind", "Choose free, paid, donation or unknown.")
    values["price_kind"] = price_kind
    if price_kind == "paid":
        try:
            amount = Decimal(str(payload.get("price_min") or "").replace(",", "")).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise SubmissionError("price_min", "Give the lowest ticket price as a number.") from None
        if amount <= 0:
            raise SubmissionError("price_min", "A paid event needs a price above zero (or choose free).")
        values["price_min"] = str(amount)
        currency = str(payload.get("currency") or destination.currency or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise SubmissionError("currency", "Give the currency as a 3-letter code, e.g. INR.")
        values["currency"] = currency
    values["ticket_url"] = _url(payload, "ticket_url")
    values["source_url"] = _url(payload, "source_url")
    return SubmissionInput(values=values, destination=destination)


def _overlaps(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return a_start[:10] <= (b_end or b_start)[:10] and b_start[:10] <= (a_end or a_start)[:10]


def find_duplicate(values: dict[str, Any], exclude_id: str | None = None) -> dict[str, str] | None:
    """An already-published or pending event in the same city with a near-identical title on overlapping dates."""
    title = normalize(values["title"])
    with SessionLocal() as db:
        published = db.scalars(select(EventFestival).where(EventFestival.destination_id == values["destination_id"], EventFestival.start_date.is_not(None))).all()
        pending = db.scalars(select(EventSubmission).where(EventSubmission.destination_id == values["destination_id"], EventSubmission.status == "pending")).all()
    for row in published:
        if (row.status or "active") not in {"cancelled", "canceled"} and name_similarity(title, row.title) >= DUPLICATE_TITLE_SIMILARITY and _overlaps(values["start_date"], values["end_date"], row.start_date, row.end_date):
            return {"kind": "published", "title": row.title, "start_date": row.start_date}
    for row in pending:
        if row.id != exclude_id and name_similarity(title, row.title) >= DUPLICATE_TITLE_SIMILARITY and _overlaps(values["start_date"], values["end_date"], row.start_date, row.end_date):
            return {"kind": "pending", "title": row.title, "start_date": row.start_date}
    return None


def create_submission(payload: dict[str, Any], user: User) -> dict[str, Any]:
    parsed = parse_submission(payload)
    with SessionLocal() as db:
        pending = db.query(EventSubmission).filter(EventSubmission.user_id == user.id, EventSubmission.status == "pending").count()
    if pending >= MAX_PENDING_PER_USER:
        raise SubmissionError("user", f"You have {pending} submissions waiting for review. Please wait until some are reviewed.", code="too_many_pending")
    duplicate = find_duplicate(parsed.values)
    if duplicate:
        where = "is already listed" if duplicate["kind"] == "published" else "is already waiting for review"
        raise SubmissionError("title", f"“{duplicate['title']}” on {duplicate['start_date'][:10]} {where}.", code="duplicate")
    row = EventSubmission(id=f"sub_{uuid.uuid4().hex[:12]}", user_id=user.id, status="pending", **parsed.values)
    with SessionLocal() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return submission_dict(row, private=True)


def submission_dict(row: EventSubmission, private: bool = False, destination_name: str | None = None) -> dict[str, Any]:
    data = {
        "id": row.id, "status": row.status, "title": row.title, "description": row.description, "category": row.category, "role": row.role,
        "organiser_name": row.organiser_name, "destination_id": row.destination_id, "city": destination_name, "start_date": row.start_date, "end_date": row.end_date,
        "start_time": row.start_time, "end_time": row.end_time, "venue_name": row.venue_name, "venue_address": row.venue_address,
        "venue_lat": row.venue_lat, "venue_lon": row.venue_lon, "price_kind": row.price_kind, "price_min": row.price_min, "currency": row.currency,
        "ticket_url": row.ticket_url, "source_url": row.source_url, "review_note": row.review_note, "event_id": row.event_id,
        "submitted_at": row.created_at.isoformat() if row.created_at else None, "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }
    if private:
        data["contact_email"] = row.contact_email
    return data


def _names(rows: list[EventSubmission]) -> dict[str, str]:
    ids = {row.destination_id for row in rows}
    with SessionLocal() as db:
        return {d.id: d.name for d in db.scalars(select(Destination).where(Destination.id.in_(ids))).all()} if ids else {}


def my_submissions(user: User) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.scalars(select(EventSubmission).where(EventSubmission.user_id == user.id).order_by(EventSubmission.created_at.desc()).limit(50)).all()
    names = _names(list(rows))
    return [submission_dict(row, private=True, destination_name=names.get(row.destination_id)) for row in rows]


def review_queue(status: str = "pending") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.scalars(select(EventSubmission).where(EventSubmission.status == status).order_by(EventSubmission.created_at.asc()).limit(100)).all()
    names = _names(list(rows))
    return [submission_dict(row, private=True, destination_name=names.get(row.destination_id)) for row in rows]


def review_submission(submission_id: str, decision: str, moderator: User, note: str | None = None) -> dict[str, Any]:
    """Approve (publish as a stored event) or reject (with a reason the submitter can read)."""
    decision = (decision or "").strip().lower()
    note = " ".join((note or "").split())[:1000] or None
    if decision not in {"approve", "reject"}:
        raise SubmissionError("decision", "Decide approve or reject.")
    if decision == "reject" and not note:
        raise SubmissionError("note", "Tell the submitter why it was rejected.")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(EventSubmission, submission_id)
        if row is None:
            raise SubmissionError("id", "Unknown submission.", code="not_found")
        if row.status != "pending":
            raise SubmissionError("status", f"This submission was already {row.status}.", code="already_reviewed")
        if decision == "approve":
            duplicate = find_duplicate(submission_dict(row), exclude_id=row.id)
            if duplicate and duplicate["kind"] == "published":
                raise SubmissionError("title", f"“{duplicate['title']}” is already listed for those dates.", code="duplicate")
            event_id = f"sub-{row.id}"
            db.merge(EventFestival(
                id=event_id, destination_id=row.destination_id, title=row.title, summary=row.description, start_date=row.start_date, end_date=row.end_date,
                start_time=row.start_time, end_time=row.end_time, event_type="festival" if row.category in {"festivals", "religious"} else "live", category=row.category,
                venue_name=row.venue_name, venue_lat=row.venue_lat, venue_lon=row.venue_lon, is_ticketed=row.price_kind == "paid", price_kind=row.price_kind,
                ticket_price=row.price_min, currency=row.currency, source=f"{row.organiser_name} (organiser submission, reviewed)",
                source_url=row.ticket_url or row.source_url, data_source_id="submission", confidence=0.7, status="active",
                last_verified_at=now.isoformat(), updated_at=now,
            ))
            row.event_id = event_id
        row.status = "approved" if decision == "approve" else "rejected"
        row.review_note, row.reviewed_by, row.reviewed_at = note, moderator.id, now
        db.commit()
        db.refresh(row)
        return submission_dict(row, private=True)
