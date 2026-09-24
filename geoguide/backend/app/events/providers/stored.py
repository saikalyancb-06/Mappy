"""Stored events: curated packs and the organisers' dataset (dated records only).

Undated "associated" festivals are never returned here as happening; see
``app.events.store.associated_festivals``.
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import and_, or_, select

from app.core.rules import load_rules
from app.db.models import EventFestival
from app.db.session import SessionLocal
from app.events.model import EventQuery, EventSource, NormalisedEvent
from app.events.providers.base import EventProvider, ProviderResult
from app.events.store import INACTIVE, city_clause
from app.events.taxonomy import categorise, price_from_text

LEGACY_LIVE = {"serpapi_events", "ticketmaster"}  # rows an older version persisted from live APIs: treated as expired cache


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _clock(value: str | None) -> time | None:
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


class StoredEventProvider(EventProvider):
    name, label = "stored", "Stored events and festivals (curated + organiser dataset)"
    local = True

    def search_events(self, query: EventQuery) -> ProviderResult:
        started = self._timed()
        result = ProviderResult(self.name, self.label)
        rules = load_rules("events")
        first, last = query.start.date().isoformat(), query.end.date().isoformat()
        with SessionLocal() as db:
            rows = db.scalars(select(EventFestival).where(
                city_clause(query.city), EventFestival.start_date.is_not(None), EventFestival.start_date <= last + "T99",
                or_(and_(EventFestival.end_date.is_not(None), EventFestival.end_date >= first), and_(EventFestival.end_date.is_(None), EventFestival.start_date >= first)),
            )).all()
        result.raw_count = len(rows)
        for row in rows:
            if (row.status or "active").lower() in INACTIVE:
                result.drop("cancelled")
                continue
            if row.id.startswith("live-") or (row.data_source_id or "") in LEGACY_LIVE:
                result.drop("legacy_live_cache")
                continue
            start_day, end_day = _day(row.start_date), _day(row.end_date) or _day(row.start_date)
            if not start_day:
                result.drop("no_date")
                continue
            starts, ends = _clock(row.start_time) or time(0, 0), _clock(row.end_time) or time(23, 59)
            legacy = rules["provider_categories"]["legacy"].get(row.category or "")
            category, categories, kind = categorise(row.title, row.summary, "dataset", [row.category] if row.category else None)
            if legacy and legacy != "other":
                category = legacy
                categories = list(dict.fromkeys([legacy, *categories]))
            kind = row.event_type if row.event_type in {"festival", "event", "live"} else kind
            curated = row.data_source_id != "ps13"
            source_kind = "stored_submission" if row.data_source_id == "submission" else "stored_curated" if curated else "stored_dataset"
            if row.price_kind in {"free", "donation"}:
                price_kind, price_min, currency = row.price_kind, "0.00" if row.price_kind == "free" else None, None
            elif row.is_ticketed or row.price_kind == "paid":
                price_kind, price_min, currency = "paid", row.ticket_price, row.currency
            else:
                price_kind, price_min, currency = price_from_text(row.summary)
            source = EventSource(provider=self.name, name=row.source or ("Curated pack" if curated else "Organiser dataset"), kind=source_kind,
                                 reliability=rules["sources"][source_kind], source_event_id=row.id, url=row.source_url,
                                 last_updated=row.last_verified_at or (row.updated_at.isoformat() + "Z" if row.updated_at else None))
            result.events.append(NormalisedEvent(
                title=row.title, description=row.summary, start=datetime.combine(start_day, starts, tzinfo=query.tz), end=datetime.combine(end_day, ends, tzinfo=query.tz),
                timezone=query.city.timezone or "UTC", all_day=not row.start_time, time_known=bool(row.start_time), source=source, category=category, categories=categories,
                type="festival" if kind == "festival" else "event", venue_name=row.venue_name, lat=row.venue_lat, lon=row.venue_lon, city=query.city.name,
                event_url=row.source_url, price_kind=price_kind, price_min=price_min, currency=currency, recurrence=row.recurrence,
                significance=row.significance, traditions=row.traditions, etiquette=row.etiquette, expected_footfall=row.expected_footfall, freshness="stored",
            ))
        result.latency_ms = int((self._timed() - started) * 1000)
        return result
