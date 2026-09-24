"""Natural-language constraint engine.

Turns requests like "somewhere within 5 km, open now, under ₹500, not crowded,
good reviews, reachable by bike in 20 minutes" or "I'm free 4–8 PM with ₹800,
alone, want something peaceful, no museums" into structured, checkable
constraints. The same constraints drive Ask, Nearby and Plan, so a request
means the same thing everywhere. Vocabulary lives in data/config/intents.json.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.core.rules import load_rules, taxonomy
from app.core.text import normalize

_CURRENCY_WORDS = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "aed": "AED"}
_MONEY = re.compile(r"(?P<pre>₹|rs\.?|inr|\$|€|£|aed)\s*(?P<amt>\d[\d,]*(?:\.\d{1,2})?)|(?P<amt2>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<post>rupees|rs\.?|inr|usd|eur|aed)\b", re.IGNORECASE)
_TIME = r"(?P<{n}h>\d{{1,2}})(?::(?P<{n}m>\d{{2}}))?\s*(?P<{n}ap>am|pm|a\.m\.|p\.m\.)?"
_WINDOW = re.compile(r"(?:from|between)?\s*" + _TIME.format(n="s") + r"\s*(?:-|–|—|to|till|until|and)\s*" + _TIME.format(n="e"), re.IGNORECASE)
_UNTIL = re.compile(r"\b(?:till|until|by)\s+" + _TIME.format(n="u"), re.IGNORECASE)
_START = re.compile(r"\b(?:start(?:ing)?\s+(?:at|from)|from|at)\s+" + _TIME.format(n="a") + r"\b", re.IGNORECASE)
_MINUTES = re.compile(r"\b(?:in|within|under|less than|max(?:imum)?|up to)\s*(?P<v>\d{1,3})\s*(?:min|mins|minutes)\b", re.IGNORECASE)
_HOURS = re.compile(r"\b(?P<v>\d+(?:\.\d+)?)\s*(?:hours|hrs|hour|hr|h)\b", re.IGNORECASE)
_RATING = re.compile(r"\b(?:rated|rating|stars?)\s*(?:above|over|at least|>=?|of)?\s*(?P<v>[1-4](?:\.\d)?)\s*\+?|(?P<v2>[1-4]\.\d)\s*\+?\s*(?:stars?|rating|rated)", re.IGNORECASE)
_LIMIT = re.compile(r"\b(?:only|just|give me|top|pick|show me)\s+(?P<n>\d{1,2}|one|two|three|four|five)\b(?:\s+(?:places?|options?|spots?|things?|ideas?))?|\b(?P<n2>\d{1,2}|one|two|three|four|five)\s+(?:places?|options?|spots?|ideas?)\s+only\b", re.IGNORECASE)
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_ROUTE_FROM_TO = re.compile(r"\bfrom\s+(?P<o>.+?)\s+to\s+(?P<d>[^?.!,]+)", re.IGNORECASE)
_ROUTE_ON_WAY = re.compile(r"\b(?:on (?:my|the) way|en route|along the way|on the route)\s+(?:to|towards)\s+(?P<d>[^?.!,]+)", re.IGNORECASE)
_ROUTE_ON_WAY_FROM = re.compile(r"\bgoing\s+(?:from\s+(?P<o>.+?)\s+)?to\s+(?P<d>[^?.!,]+?)(?:\s*[,.]|\s+(?:find|show|suggest|what|anything)|$)", re.IGNORECASE)


@dataclass
class Constraints:
    available_minutes: int | None = None
    window_start: str | None = None  # "HH:MM" local
    window_end: str | None = None
    max_cost: str | None = None  # decimal text
    cost_currency: str | None = None
    max_price_level: int | None = None
    min_rating: float | None = None
    max_travel_min: int | None = None
    travel_mode: str | None = None
    open_now: bool = False
    crowd: str | None = None  # "low"
    party: str | None = None
    limit: int | None = None
    ranking_mode: str | None = None  # popular | local | hidden_gems
    include_categories: list[str] = field(default_factory=list)
    include_groups: list[str] = field(default_factory=list)
    exclude_categories: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    route_origin: str | None = None
    route_destination: str | None = None
    raining: bool = False
    understood: list[str] = field(default_factory=list)  # human-readable interpretation

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return not any([self.available_minutes, self.window_start, self.max_cost, self.max_price_level, self.min_rating, self.max_travel_min, self.travel_mode, self.open_now, self.crowd, self.limit, self.ranking_mode, self.include_categories, self.include_groups, self.exclude_categories, self.avoid_tags, self.route_destination])


def _has(norm: str, phrases: list[str]) -> str | None:
    for phrase in sorted(phrases, key=len, reverse=True):
        target = normalize(phrase)
        if target and re.search(rf"(?:^|\s){re.escape(target)}(?:\s|$)", norm):
            return phrase
    return None


def _clock(hour: str | None, minute: str | None, ampm: str | None, context_pm: bool = False) -> str | None:
    if hour is None:
        return None
    h = int(hour)
    m = int(minute or 0)
    marker = (ampm or "").lower().replace(".", "")
    if marker == "pm" and h < 12:
        h += 12
    elif marker == "am" and h == 12:
        h = 0
    elif not marker and context_pm and h < 12:
        h += 12
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _negated_spans(text: str, negations: list[str]) -> list[str]:
    spans = []
    pattern = r"\b(?:" + "|".join(re.escape(n) for n in sorted(negations, key=len, reverse=True)) + r")\b(?P<span>[^,.;!?]*)"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        span = re.split(r"\b(?:but|and i|and we|i want|i'd like|however)\b", match.group("span"), maxsplit=1, flags=re.IGNORECASE)[0]
        spans.append(span)
    return spans


def _categories_in(norm: str) -> tuple[list[str], list[str]]:
    cats, groups = [], []
    for category_id, entry in taxonomy()["categories"].items():
        if _has(norm, entry["synonyms"]):
            cats.append(category_id)
    for group_id, entry in taxonomy()["groups"].items():
        if _has(norm, [entry["label"], group_id]) and group_id not in {"stay", "services"}:
            groups.append(group_id)
    return cats, groups


def parse_constraints(text: str) -> Constraints:
    rules = load_rules("intents")["constraints"]
    raw = " ".join((text or "").split())
    lowered = raw.lower()
    norm = normalize(raw)
    c = Constraints()

    # Exclusions first, so negated words don't also count as wishes.
    negated_text = " ".join(_negated_spans(lowered, rules["negations"]))
    negated_norm = normalize(negated_text)
    if negated_norm:
        c.exclude_categories, excluded_groups = _categories_in(negated_norm)
        for group in excluded_groups:
            from app.core.rules import group_categories

            c.exclude_categories.extend(group_categories(group))
        for tag, words in rules["negatable_tags"].items():
            if _has(negated_norm, words):
                c.avoid_tags.append(tag)
    positive_norm = norm
    for span in _negated_spans(lowered, rules["negations"]):
        positive_norm = positive_norm.replace(normalize(span), " ")
    cats, groups = _categories_in(positive_norm)
    c.include_categories = [x for x in cats if x not in c.exclude_categories]
    c.include_groups = groups
    for preference, phrases in taxonomy()["preferences"].items():
        if _has(positive_norm, phrases):
            c.preferences.append(preference)
    if "crowded" in c.avoid_tags or "peaceful" in c.preferences or _has(norm, ["less crowded", "not crowded", "uncrowded", "quiet"]):
        c.crowd = "low"
        if "crowded" not in c.avoid_tags:
            c.avoid_tags.append("crowded")

    # Money: the first amount near a budget word, or any explicit currency amount.
    money = _MONEY.search(raw)
    if money:
        amount = (money.group("amt") or money.group("amt2") or "").replace(",", "")
        symbol = (money.group("pre") or money.group("post") or "").lower()
        if amount:
            c.max_cost = str(Decimal(amount).quantize(Decimal("0.01")))
            c.cost_currency = _CURRENCY_WORDS.get(symbol, "INR")
    symbols = re.search(r"(₹{1,4}|\${1,4})(?!\s*\d)", raw)
    if symbols:
        c.max_price_level = len(symbols.group(1))
    for level, phrases in rules["cheap_phrases"].items():
        if _has(positive_norm, phrases) and c.max_price_level is None and level != "4":
            c.max_price_level = int(level)

    rating = _RATING.search(raw)
    if rating:
        c.min_rating = float(rating.group("v") or rating.group("v2"))
    elif _has(norm, rules["good_rating_phrases"]):
        c.min_rating = float(rules["good_rating_threshold"])

    for mode, phrases in rules["travel_modes"].items():
        if _has(norm, phrases):
            c.travel_mode = mode
            break
    minutes = _MINUTES.search(raw)
    if minutes and (c.travel_mode or re.search(r"\b(reach|get there|travel|drive|ride|walk)\b", lowered)):
        c.max_travel_min = int(minutes.group("v"))

    c.open_now = bool(re.search(r"\bopen (?:right )?now\b|\bcurrently open\b|\bstill open\b", lowered))
    for party, phrases in rules["parties"].items():
        if _has(norm, phrases):
            c.party = party
            if party == "family" and "family" not in c.preferences:
                c.preferences.append("family")
            break
    for mode, phrases in rules["ranking_modes"].items():
        if _has(norm, phrases):
            c.ranking_mode = mode
            break
    c.raining = bool(_has(norm, rules["rain_phrases"])) and "weather" not in norm

    limit = _LIMIT.search(lowered)
    if limit:
        token = limit.group("n") or limit.group("n2")
        c.limit = _WORD_NUM.get(token, None) or int(token)

    window = _WINDOW.search(raw)
    if window and (window.group("sap") or window.group("eap") or re.search(r"\b(?:free|from|between)\b", lowered)):
        end_pm = (window.group("eap") or "").lower().startswith("p")
        c.window_end = _clock(window.group("eh"), window.group("em"), window.group("eap"))
        c.window_start = _clock(window.group("sh"), window.group("sm"), window.group("sap") or (window.group("eap") if int(window.group("sh")) <= int(window.group("eh")) or end_pm else None))
        if c.window_start and c.window_end and c.window_end > c.window_start:
            sh, sm = map(int, c.window_start.split(":"))
            eh, em = map(int, c.window_end.split(":"))
            c.available_minutes = (eh * 60 + em) - (sh * 60 + sm)
        else:
            c.window_start = c.window_end = None
    if c.available_minutes is None:
        until = _UNTIL.search(raw)
        if until and until.group("uap"):
            c.window_end = _clock(until.group("uh"), until.group("um"), until.group("uap"))
        start = _START.search(raw)
        if start and start.group("aap"):
            c.window_start = _clock(start.group("ah"), start.group("am"), start.group("aap"))
        hours = _HOURS.search(raw)
        if hours:
            c.available_minutes = int(float(hours.group("v")) * 60)
        elif re.search(r"\bhalf\s+day\b", lowered):
            c.available_minutes = 240
        elif re.search(r"\b(?:full|whole|entire)\s+day\b", lowered):
            c.available_minutes = 540
        elif minutes and not c.max_travel_min and re.search(r"\b(?:i have|have|got|free for|spare)\b", lowered):
            c.available_minutes = int(minutes.group("v"))

    route = _ROUTE_FROM_TO.search(raw)
    on_way = _ROUTE_ON_WAY.search(raw)
    if on_way:
        c.route_destination = on_way.group("d").strip()
    elif route and re.search(r"\b(?:way|route|going|travelling|traveling|driving|riding|heading|between)\b", lowered):
        c.route_origin, c.route_destination = route.group("o").strip(), route.group("d").strip()
    else:
        going = _ROUTE_ON_WAY_FROM.search(raw)
        if going and re.search(r"\b(?:on the way|on my way|along|interesting|stop|detour|en route)\b", lowered):
            c.route_origin, c.route_destination = (going.group("o") or None), going.group("d").strip()
    for attr in ("route_origin", "route_destination"):
        value = getattr(c, attr)
        if value:
            value = re.sub(r"\b(?:find|show|suggest|anything|something|and|with|please)\b.*$", "", value, flags=re.IGNORECASE).strip(" ,.")
            setattr(c, attr, value or None)
    if c.route_destination:
        # Words inside place names ("Vittala *Temple*") are not what the traveller is looking for.
        rest = raw
        for endpoint in (c.route_origin, c.route_destination):
            if endpoint:
                rest = re.sub(re.escape(endpoint), " ", rest, flags=re.IGNORECASE)
        inner = parse_constraints(rest)
        c.include_categories, c.include_groups, c.exclude_categories = inner.include_categories, inner.include_groups, inner.exclude_categories
        c.preferences = inner.preferences

    c.understood = describe(c)
    return c


def describe(c: Constraints) -> list[str]:
    labels = {k: v["label"] for k, v in taxonomy()["categories"].items()}
    group_labels = {k: v["label"] for k, v in taxonomy()["groups"].items()}
    out = []
    if c.window_start and c.window_end:
        out.append(f"Time: {c.window_start}–{c.window_end}")
    elif c.available_minutes:
        out.append(f"Time available: {c.available_minutes // 60} h {c.available_minutes % 60} min".replace(" 0 min", ""))
    if c.max_cost:
        out.append(f"Spend up to {c.cost_currency or ''} {c.max_cost}".strip())
    if c.max_price_level:
        out.append("Price level ≤ " + "₹" * c.max_price_level)
    if c.include_categories or c.include_groups:
        grouped = {x for g in c.include_groups for x in taxonomy()["groups"].get(g, {}).get("categories", [])}
        out.append("Looking for: " + ", ".join([group_labels[g] for g in c.include_groups] + [labels[x] for x in c.include_categories if x in labels and x not in grouped]))
    if c.exclude_categories:
        out.append("Avoiding: " + ", ".join(sorted({labels.get(x, x) for x in c.exclude_categories})))
    if c.avoid_tags:
        out.append("Not: " + ", ".join(c.avoid_tags))
    if c.preferences:
        out.append("Preferences: " + ", ".join(p.replace("_", " ") for p in c.preferences))
    if c.min_rating:
        out.append(f"Rating ≥ {c.min_rating:g}")
    if c.travel_mode:
        out.append(f"Travel by {c.travel_mode}" + (f", ≤ {c.max_travel_min} min" if c.max_travel_min else ""))
    elif c.max_travel_min:
        out.append(f"Reachable in ≤ {c.max_travel_min} min")
    if c.open_now:
        out.append("Open now")
    if c.party:
        out.append(f"Going {c.party}" if c.party == "solo" else f"With {c.party}")
    if c.ranking_mode:
        out.append({"hidden_gems": "Hidden gems", "local": "Local favourites", "popular": "Popular picks"}[c.ranking_mode])
    if c.limit:
        out.append(f"Only {c.limit} options")
    if c.route_destination:
        out.append(f"On the way {'from ' + c.route_origin + ' ' if c.route_origin else ''}to {c.route_destination}")
    if c.raining:
        out.append("It's raining")
    return out
