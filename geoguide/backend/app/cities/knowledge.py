"""City knowledge documents from Wikipedia (attributed, never LLM-generated).

One request fetches the article's plain-text extract; its lead becomes the
``overview`` and its sections are mapped onto knowledge types (history, culture,
geography, climate, food, transport, neighbourhoods, attractions) by heading.
The article must not be a disambiguation page, and when it has coordinates they
must lie inside the city's boundary, so a same-named place elsewhere is never
used. Stored as ``knowledge_chunks`` with ``kind="city_kb"``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.config import WIKIPEDIA_API_URL
from app.core.http import ProviderError, get_json
from app.core.rules import load_rules
from app.core.text import normalize
from app.db.models import Destination, KnowledgeChunk
from app.db.session import SessionLocal
from app.geo.distance import haversine_km
from app.places.boundary import boundary_km

_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\n{2,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind(".\n"))
    return (cut[: end + 1] if end > limit * 0.5 else cut).strip()


def split_sections(extract: str) -> tuple[str, list[tuple[str, str]]]:
    """(lead, [(top-level heading, text including its subsections)])."""
    matches = list(_HEADING.finditer(extract))
    lead = extract[: matches[0].start()] if matches else extract
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        if len(match.group(1)) != 2:
            continue
        end = next((m.start() for m in matches[index + 1:] if len(m.group(1)) == 2), len(extract))
        body = _HEADING.sub("", extract[match.end():end])
        sections.append((match.group(2), body))
    return lead, sections


def map_sections(lead: str, sections: list[tuple[str, str]]) -> dict[str, str]:
    rules = load_rules("city_intelligence")
    limit = rules["knowledge_max_chars"]
    out: dict[str, str] = {}
    if lead.strip():
        out["overview"] = _trim(lead, limit)
    for kind, headings in rules["knowledge_sections"].items():
        if kind == "overview":
            continue
        wanted = [normalize(h) for h in headings]
        parts = [body for heading, body in sections if any(normalize(heading) == w or normalize(heading).startswith(w + " ") for w in wanted) and body.strip()]
        if parts:
            out[kind] = _trim("\n\n".join(parts), limit)
    return out


def _page(title: str) -> dict[str, Any] | None:
    payload = get_json("wikipedia", WIKIPEDIA_API_URL, params={
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1, "titles": title,
        "prop": "extracts|coordinates|info|pageprops", "explaintext": 1, "exsectionformat": "wiki", "inprop": "url",
    }, timeout=10.0) or {}
    pages = (payload.get("query") or {}).get("pages") or []
    page = pages[0] if pages else None
    if not page or page.get("missing") or "disambiguation" in (page.get("pageprops") or {}):
        return None
    return page


def find_article(destination: Destination) -> tuple[dict[str, Any] | None, int]:
    """(article, requests made)."""
    requests = 0
    candidates = list(dict.fromkeys(t for t in (f"{destination.name}, {destination.region}" if destination.region else None, destination.name, f"{destination.name}, {destination.country}" if destination.country else None) if t))
    for title in candidates:
        requests += 1
        page = _page(title)
        if not page or not (page.get("extract") or "").strip():
            continue
        coordinates = (page.get("coordinates") or [{}])[0]
        if coordinates.get("lat") is not None and haversine_km(destination.lat, destination.lon, float(coordinates["lat"]), float(coordinates["lon"])) > boundary_km(destination) + 10:
            continue  # a same-named place somewhere else
        return page, requests
    return None, requests


def collect(destination: Destination) -> tuple[int, int, dict[str, str] | None]:
    """Fetch and store the city's knowledge documents. Returns (documents stored, requests made, error)."""
    try:
        page, requests = find_article(destination)
    except ProviderError as exc:
        return 0, 1, exc.as_dict()
    if page is None:
        return 0, requests, None
    lead, sections = split_sections(page["extract"])
    documents = map_sections(lead, sections)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    url = page.get("fullurl") or f"https://en.wikipedia.org/wiki/{page.get('title', destination.name).replace(' ', '_')}"
    with SessionLocal() as db:
        for kind, content in documents.items():
            chunk_id = f"citykb-{destination.id}-{kind}"
            existing = db.get(KnowledgeChunk, chunk_id)
            if existing is not None and existing.content == content:
                existing.updated_at = now
                continue
            db.merge(KnowledgeChunk(
                id=chunk_id, document_id=chunk_id, destination_id=destination.id, kind="city_kb", category=kind,
                title=f"{destination.name}: {kind.replace('_', ' ').title()}", content=content, language="en",
                source=f"Wikipedia — {page.get('title', destination.name)} (CC BY-SA)", source_url=url, data_source_id="wikipedia",
                confidence=0.8, created_at=existing.created_at if existing else now, updated_at=now, embedding=None, embedding_model=None, embedding_dim=None,
            ))
        city = db.get(Destination, destination.id)
        if city is not None and not city.summary and documents.get("overview"):
            city.summary = " ".join(re.split(r"(?<=[.!?])\s+", documents["overview"])[:2])
        db.commit()
    return len(documents), requests, None
