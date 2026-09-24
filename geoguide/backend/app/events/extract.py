"""Reading event facts out of listings and web results — conservatively.

Dates and times are parsed only when written explicitly; anything unreadable is left
empty (and the listing dropped or down-weighted), never guessed.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time

from app.core.dates import MONTHS, parse_day

_RANGE_TAIL = re.compile(r"[–—-]\s*(?:[A-Za-z]{3,9},?\s*)?(?P<rest>[A-Za-z]{3,9}\.?\s+\d{1,2}|\d{1,2}(?:\s+[A-Za-z]{3,9}\.?)?)\b")
_CLOCK = r"(?P<{n}h>\d{{1,2}})(?::(?P<{n}m>\d{{2}}))?\s*(?P<{n}ap>am|pm|a\.m\.|p\.m\.)?"
_TIME_RANGE = re.compile(_CLOCK.format(n="s") + r"\s*[–—-]\s*" + _CLOCK.format(n="e"), re.IGNORECASE)
_TIME_ONE = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)", re.IGNORECASE)
_VENUE = re.compile(r"\b(?:at|venue:?|held at|location:?)\s+(?:the\s+)?(?P<v>[A-Z][\w'’&.-]*(?:\s+(?:of|and|&|de)?\s*[A-Z][\w'’&.-]*){0,5})")
_TITLE_SUFFIX = re.compile(r"\s*(?:[|–—]\s*[^|–—]{2,60}|\s-\s[^-]{2,40})$")


def listing_dates(start_text: str | None, when_text: str | None, anchor: date) -> tuple[date, date] | None:
    """A listing's dates ("Oct 22", "Thu, Oct 22, 7 – 10 PM", "Oct 20 – 25", "Sat, Oct 24 – Sun, Oct 25", "20-25 October")."""
    start = parse_day(start_text or "", anchor) or parse_day(when_text or "", anchor)
    if start is None:
        return None
    end = start
    source = when_text or start_text or ""
    for tail in _RANGE_TAIL.finditer(source):
        rest = tail.group("rest").strip()
        candidate = None
        if rest.isdigit():
            try:
                candidate = start.replace(day=int(rest))
            except ValueError:
                candidate = None
        else:
            candidate = parse_day(rest, start)
            if candidate is None and re.match(r"\d{1,2}\s+[A-Za-z]", rest):
                candidate = parse_day(rest, start)
        if candidate and candidate >= start and (candidate - start).days <= 120:
            end = candidate
            break
    return start, end


def _clock(hour: str, minute: str | None, ampm: str | None, fallback_pm: bool = False) -> time | None:
    h, m = int(hour), int(minute or 0)
    marker = (ampm or "").lower().replace(".", "")
    if marker == "pm" and h < 12:
        h += 12
    elif marker == "am" and h == 12:
        h = 0
    elif not marker and fallback_pm and h < 12:
        h += 12
    return time(h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def listing_times(text: str | None) -> tuple[time | None, time | None]:
    """Start/end clock times written in a listing ("7 – 10 PM", "7:30 pm"); (None, None) if none."""
    if not text:
        return None, None
    span = _TIME_RANGE.search(text)
    if span and (span.group("sap") or span.group("eap")):
        end_pm = (span.group("eap") or "").lower().startswith("p")
        start = _clock(span.group("sh"), span.group("sm"), span.group("sap"), fallback_pm=end_pm and int(span.group("sh")) <= int(span.group("eh")))
        return start, _clock(span.group("eh"), span.group("em"), span.group("eap"))
    one = _TIME_ONE.search(text)
    return (_clock(one.group("h"), one.group("m"), one.group("ap")), None) if one else (None, None)


def venue_from_text(text: str | None) -> str | None:
    match = _VENUE.search(text or "")
    if not match:
        return None
    venue = match.group("v").strip(" .,")
    first = venue.split()[0].lower().rstrip(".")
    return None if first in MONTHS or len(venue) < 3 else venue


def clean_title(title: str) -> str:
    cleaned = _TITLE_SUFFIX.sub("", title.strip())
    cleaned = re.sub(r"\b(?:tickets?|book now|buy tickets)\b[:\s]*", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip(" -|:") or title.strip()


def combine(day: date, clock: time | None, tz) -> datetime:
    return datetime.combine(day, clock or time(0, 0), tzinfo=tz)
