"""Date ranges for city-and-date context: "today", "this weekend", "Oct 22", "in October"…

Everything is resolved against the *selected* date (which defaults to the
city's local today), so follow-up questions stay on the date the traveller is
looking at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {name: index for index, names in enumerate([
    (), ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december"),
]) for name in names}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m>{_MONTH})\.?(?:,?\s+(?P<y>\d{{4}}))?\b", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b(?P<m>{_MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{{4}}))?\b", re.IGNORECASE)
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
_IN_MONTH = re.compile(rf"\b(?:in|during|for|this|next)?\s*(?P<m>{_MONTH})\b(?:\s+(?P<y>\d{{4}}))?", re.IGNORECASE)


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date
    label: str  # human wording, e.g. "Sat 26 – Sun 27 Sep 2026"
    kind: str = "day"  # day | weekend | week | month | range

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label, "kind": self.kind}


def fmt_day(day: date) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b %Y')}"


def fmt_range(start: date, end: date) -> str:
    if start == end:
        return fmt_day(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%a')} {start.day} – {end.strftime('%a')} {end.day} {end.strftime('%b %Y')}"
    return f"{fmt_day(start)} – {fmt_day(end)}"


def single(day: date) -> DateRange:
    return DateRange(day, day, fmt_day(day), "day")


def weekend_of(day: date) -> DateRange:
    """The weekend containing ``day``, or the next one if ``day`` is a weekday."""
    saturday = day + timedelta(days=(5 - day.weekday()) % 7) if day.weekday() < 5 else day - timedelta(days=day.weekday() - 5)
    start = max(day, saturday)
    end = saturday + timedelta(days=1)
    return DateRange(start, end, fmt_range(start, end), "weekend")


def week_from(day: date) -> DateRange:
    end = day + timedelta(days=6)
    return DateRange(day, end, fmt_range(day, end), "week")


def month_of(year: int, month: int) -> DateRange:
    start = date(year, month, 1)
    end = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    return DateRange(start, end, start.strftime("%B %Y"), "month")


def _year_near(month: int, day: int, anchor: date) -> int:
    """The year that puts month/day closest to the anchor (so "Jan 3" in December means next year)."""
    options = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            options.append((abs((date(year, month, day) - anchor).days), year))
        except ValueError:
            continue
    return min(options)[1] if options else anchor.year


def parse_day(text: str, anchor: date) -> date | None:
    """A single calendar day mentioned in text ("Oct 22", "22 October 2026", "2026-10-22")."""
    iso = _ISO.search(text)
    if iso:
        try:
            return date(int(iso.group("y")), int(iso.group("m")), int(iso.group("d")))
        except ValueError:
            return None
    for pattern in (_MONTH_DAY, _DAY_MONTH):
        match = pattern.search(text)
        if match:
            month, day = MONTHS[match.group("m").lower().rstrip(".")], int(match.group("d"))
            year = int(match.group("y")) if match.group("y") else _year_near(month, day, anchor)
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def parse_range(text: str, anchor: date) -> DateRange | None:
    """The date range a question refers to, resolved against the selected date."""
    lowered = (text or "").lower()
    if re.search(r"\b(?:this|the)\s+weekend\b|\bweekend\b", lowered):
        base = anchor + timedelta(days=7) if re.search(r"\bnext\s+weekend\b", lowered) else anchor
        return weekend_of(base)
    if re.search(r"\bnext\s+week\b", lowered):
        monday = anchor + timedelta(days=7 - anchor.weekday())
        end = monday + timedelta(days=6)
        return DateRange(monday, end, fmt_range(monday, end), "week")
    if re.search(r"\b(?:this\s+week|coming\s+week|next\s+(?:7|seven)\s+days)\b", lowered):
        return week_from(anchor)
    if re.search(r"\btomorrow\b", lowered):
        return single(anchor + timedelta(days=1))
    if re.search(r"\byesterday\b", lowered):
        return single(anchor - timedelta(days=1))
    day = parse_day(text, anchor)
    if day:
        return single(day)
    if re.search(r"\bthis\s+month\b", lowered):
        return month_of(anchor.year, anchor.month)
    if re.search(r"\bnext\s+month\b", lowered):
        return month_of(anchor.year + (anchor.month == 12), anchor.month % 12 + 1)
    month = _IN_MONTH.search(lowered)
    if month and re.search(rf"\b(?:in|during|for|this|next)\s+(?:{_MONTH})\b", lowered):
        index = MONTHS[month.group("m").lower()]
        year = int(month.group("y")) if month.group("y") else (anchor.year if index >= anchor.month else anchor.year + 1)
        return month_of(year, index)
    if re.search(r"\b(?:today|tonight|now|right now|currently|at the moment|happening here)\b", lowered):
        return single(anchor)
    return None
