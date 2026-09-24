"""Source reliability for web-discovered events (kinds of sources, configured in events.json)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.rules import load_rules


def domain(url: str | None) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.") if url else ""


def web_source_kind(url: str | None) -> tuple[str, float]:
    """(kind, reliability) for a web page: authoritative > event platform > news > aggregator > unknown."""
    rules = load_rules("events")["sources"]
    host = domain(url)
    if not host:
        return "web_unknown", rules["web_unknown"]
    if any(pattern in host for pattern in rules["aggregator_domains"]):
        return "web_aggregator", rules["web_aggregator"]
    if any(pattern in host for pattern in rules["authoritative_patterns"]):
        return "web_authoritative", rules["web_authoritative"]
    if any(pattern in host for pattern in rules["platform_domains"]):
        return "web_platform", rules["web_platform"]
    if any(pattern in host for pattern in rules["news_domains"]):
        return "web_news", rules["web_news"]
    return "web_unknown", rules["web_unknown"]


def is_listicle(title: str) -> bool:
    """Round-ups ("Top 10 events in …", "Things to do this weekend") are not single events."""
    lowered = title.lower()
    return any(re.search(pattern, lowered) for pattern in load_rules("events")["sources"]["listicle_patterns"])
