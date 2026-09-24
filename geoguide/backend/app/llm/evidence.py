"""Evidence builder: turns retrieved data into a bounded, citable evidence list."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models import Candidate, Evidence

_CURRENCY = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _money(amount: Any, currency: str | None) -> str | None:
    """Format exact decimal money (text or number) without passing through float arithmetic."""
    if amount is None or amount == "":
        return None
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return None
    if value == 0:
        return "free"
    symbol = _CURRENCY.get(currency or "", (currency or "") + " ")
    text = f"{value:,.2f}"
    return f"{symbol}{text[:-3] if text.endswith('.00') else text}"


def _distance(km: float | None) -> str | None:
    if km is None:
        return None
    return f"{int(round(km * 1000, -1))} m" if km < 1 else f"{km:.1f} km"


def describe_candidate(candidate: Candidate, reference_label: str | None, distance_from_user_km: float | None = None) -> str:
    parts = [f"{candidate.name}"]
    if candidate.category:
        parts.append(f"category: {candidate.category}")
    if candidate.neighborhood:
        parts.append(f"area: {candidate.neighborhood}")
    if candidate.address and candidate.address != candidate.neighborhood:
        parts.append(f"address: {candidate.address}")
    if candidate.distance_km is not None and reference_label:
        parts.append(f"distance from {reference_label}: {_distance(candidate.distance_km)}")
    if distance_from_user_km is not None:
        parts.append(f"distance from the traveller: {_distance(distance_from_user_km)}")
    if candidate.open_status != "unknown":
        detail = candidate.open_detail
        status = f"currently {candidate.open_status}"
        if detail.get("closes_at"):
            status += f" (closes {detail['closes_at']})"
        if detail.get("opens_at"):
            status += f" (opens {detail['opens_at']})"
        parts.append(status)
    else:
        parts.append("open status: unknown")
    if candidate.opening_hours_text:
        parts.append(f"hours: {candidate.opening_hours_text}")
    fee = _money(candidate.entry_cost if candidate.entry_cost is not None else candidate.entry_fee, candidate.fee_currency)
    if fee:
        foreign = _money(candidate.entry_fee_foreign, candidate.fee_currency)
        parts.append(f"entry: {fee}" + (f" (foreign visitors {foreign})" if foreign and foreign != fee else ""))
    if candidate.fee_notes:
        parts.append(f"fee notes: {candidate.fee_notes}")
    if candidate.visit_duration_min:
        parts.append(f"typical visit: {candidate.visit_duration_min} min")
    if candidate.star_rating:
        parts.append(f"{candidate.star_rating}-star {candidate.property_type or 'stay'}")
    if candidate.guest_score is not None:
        parts.append(f"guest score: {candidate.guest_score:.1f}/10" + (f" from {candidate.review_count} reviews" if candidate.review_count else ""))
    elif candidate.rating is not None:
        parts.append(f"rating: {candidate.rating:.1f}" + (f" from {candidate.review_count} reviews" if candidate.review_count else ""))
    if candidate.price_per_night:
        parts.append(f"price per night: {_money(candidate.price_per_night, candidate.price_currency)} ({candidate.price_source}, retrieved {str(candidate.price_retrieved_at or '')[:10]})")
    elif candidate.kind == "stay":
        parts.append("price per night: not available in the data")
    if candidate.checkin_time:
        parts.append(f"check-in {candidate.checkin_time}, check-out {candidate.checkout_time}")
    cost = candidate.cost_for_user
    if cost.get("fits_budget") is not None:
        parts.append(f"{'within' if cost['fits_budget'] else 'over'} the traveller's budget ({cost.get('note')})")
    if candidate.popularity_score is not None:
        parts.append(f"popularity: {candidate.popularity_score}/100")
    if candidate.detour_min is not None:
        parts.append(f"detour: about {candidate.detour_min} min (estimate)")
    elif candidate.travel_min is not None and candidate.travel_mode:
        parts.append(f"travel: about {candidate.travel_min} min by {candidate.travel_mode} (estimate)")
    if candidate.conflicts:
        parts.append("SOURCES DISAGREE: " + "; ".join(c["detail"] for c in candidate.conflicts) + " — tell the traveller to verify before going")
    if candidate.confidence_detail:
        parts.append(f"information confidence: {candidate.confidence_detail.get('label')}")
    if candidate.step_free is not None:
        parts.append("step-free: " + ("yes" if candidate.step_free else "no"))
    if candidate.accessibility_notes:
        parts.append(f"access: {candidate.accessibility_notes}")
    if candidate.walking_effort:
        parts.append(f"walking effort: {candidate.walking_effort}")
    if candidate.best_time:
        parts.append(f"best time: {candidate.best_time}")
    if candidate.tags:
        parts.append("tags: " + ", ".join(candidate.tags[:8]))
    if candidate.description:
        parts.append(candidate.description[:300])
    return "; ".join(parts)


@dataclass
class EvidenceBuilder:
    items: list[Evidence] = field(default_factory=list)

    def _next_id(self) -> str:
        return f"E{len(self.items) + 1}"

    def add(self, source_type: str, title: str, content: str, **kwargs: Any) -> Evidence:
        evidence = Evidence(id=self._next_id(), source_type=source_type, title=title, content=content, **kwargs)
        self.items.append(evidence)
        return evidence

    def add_candidate(self, candidate: Candidate, reference_label: str | None, distance_from_user_km: float | None = None) -> Evidence:
        primary = candidate.primary_source
        return self.add(
            "poi",
            candidate.name,
            describe_candidate(candidate, reference_label, distance_from_user_km),
            source=primary.source if primary else None,
            source_url=(primary.source_url if primary else None) or candidate.website,
            source_id=candidate.id,
            retrieved_at=primary.retrieved_at if primary else None,
            confidence=candidate.confidence,
            metadata={
                "candidate_id": candidate.id,
                "rating": candidate.rating,
                "distance_km": candidate.distance_km,
                "distance_from_user_km": distance_from_user_km,
                "open_status": candidate.open_status,
                "source_types": [s.source_type for s in candidate.sources],
            },
        )

    def add_knowledge(self, hit: Any) -> Evidence:
        return self.add("knowledge", hit.title or "Place knowledge", hit.content, source=hit.source, source_url=hit.source_url, source_id=hit.chunk_id, retrieved_at=hit.updated_at, confidence=hit.confidence, metadata={"kind": hit.kind, "poi_id": hit.poi_id, "scores": hit.scores})

    def add_weather(self, weather: dict[str, Any], place_label: str) -> Evidence | None:
        if weather.get("status") != "ok":
            return None
        parts = []
        current = weather.get("current")
        if current:
            parts.append(f"Now in {place_label}: {current['summary']}, {current['temperature_c']}°C (feels like {current['apparent_c']}°C), humidity {current['humidity_pct']}%")
        day = weather.get("day")
        if day and weather.get("live") is False:
            parts.append(f"Dataset daily weather record for {day['date']} (not a live forecast): {day['summary']}, {day['temp_min_c']}–{day['temp_max_c']}°C, feels like {day['apparent_max_c']}°C, precipitation {day['precipitation_mm']} mm, humidity {day.get('humidity_pct')}%")
        elif day:
            parts.append(f"Forecast for {day['date']}: {day['summary']}, {day['temp_min_c']}–{day['temp_max_c']}°C, feels like up to {day['apparent_max_c']}°C, precipitation chance up to {day['precipitation_probability_max']}%, UV index {day['uv_index_max']}, sunrise {str(day['sunrise'])[-5:]}, sunset {str(day['sunset'])[-5:]}")
        if weather.get("daylight_left_min") is not None:
            parts.append(f"Daylight left today: {weather['daylight_left_min'] // 60} h {weather['daylight_left_min'] % 60} min")
        return self.add("weather", f"Weather for {place_label}", ". ".join(parts), source="Open-Meteo" if weather.get("live", True) else "Dataset weather_daily (not live)", source_url=weather.get("source_url"), retrieved_at=weather.get("retrieved_at"), confidence=0.9, metadata={"signals": weather.get("signals"), "day": day, "current": current})

    def add_advisory(self, advisory: dict[str, Any]) -> Evidence:
        label = "Forecast-derived notice" if advisory.get("kind") == "weather_derived" else "Safety advisory"
        return self.add("safety", advisory["title"], f"{label} (severity: {advisory['severity']}): {advisory.get('body') or advisory['title']}", source=advisory.get("source"), source_url=advisory.get("source_url"), source_id=advisory.get("id"), confidence=0.9, metadata={"severity": advisory["severity"], "kind": advisory.get("kind"), "body": advisory.get("body")})

    def add_event(self, event: dict[str, Any]) -> Evidence:
        timing = {"dated": f"{event.get('start_date')} to {event.get('end_date') or event.get('start_date')}", "usually_this_time_of_year": "usually held around this time of year; exact dates vary", "dates_vary": "dates vary each year", "other_season": "held in another season"}.get(event.get("timing"), "")
        return self.add("event", event["title"], f"{event['title']}: {event.get('summary') or ''} Timing: {timing}.".strip(), source=event.get("source"), source_url=event.get("source_url"), source_id=event.get("id"), confidence=event.get("confidence"))

    def add_web(self, item: dict[str, Any]) -> Evidence:
        return self.add("web", item["title"], item.get("snippet") or item["title"], source=item.get("source_domain"), source_url=item.get("url"), retrieved_at=item.get("retrieved_at"), confidence=item.get("confidence"), metadata={"published": item.get("published"), "engine": item.get("engine")})
