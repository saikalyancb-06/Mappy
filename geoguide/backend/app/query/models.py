from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class IntentType(str, Enum):
    NEARBY_SEARCH = "NEARBY_SEARCH"
    PLACE_LOOKUP = "PLACE_LOOKUP"
    PLACE_DISAMBIGUATION = "PLACE_DISAMBIGUATION"
    DESTINATION_KNOWLEDGE = "DESTINATION_KNOWLEDGE"
    ACTIVITY_DISCOVERY = "ACTIVITY_DISCOVERY"
    RECOMMENDATION = "RECOMMENDATION"
    WEATHER = "WEATHER"
    SAFETY = "SAFETY"
    ITINERARY = "ITINERARY"
    LIVE_INFORMATION = "LIVE_INFORMATION"
    WEB_RESEARCH = "WEB_RESEARCH"
    GENERAL_TRAVEL_QUESTION = "GENERAL_TRAVEL_QUESTION"
    ROUTE_SUGGESTIONS = "ROUTE_SUGGESTIONS"
    COMPARE = "COMPARE"


@dataclass
class TemporalConstraint:
    label: str  # now | today | tonight | tomorrow | weekend | duration
    day_offset: int = 0
    part_of_day: str | None = None  # morning | afternoon | evening | night
    duration_hours: float | None = None


@dataclass
class QueryIntent:
    intent: IntentType
    raw_query: str
    category: str | None = None  # taxonomy category id
    category_group: str | None = None  # taxonomy group id when the query names a group ("heritage")
    entity_mention: str | None = None  # a specific place the user names
    locality_hint: str | None = None  # neighbourhood/area qualifying the entity ("in Gandhi Bazaar")
    place_mention: str | None = None  # destination / anchor named after in/at/near/around
    spatial_relation: str | None = None  # near_me | near_place | in_place | pronoun | None
    radius_km: float | None = None
    temporal: TemporalConstraint | None = None
    preferences: list[str] = field(default_factory=list)
    knowledge_required: bool = False
    weather_required: bool = False
    live_required: bool = False
    web_required: bool = False
    safety_required: bool = False
    events_required: bool = False
    quality_focus: bool = False
    confidence: float = 0.5
    parser: str = "rules"
    signals: list[str] = field(default_factory=list)
    constraints: Any = None  # app.query.constraints.Constraints
    compare_mentions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        constraints = self.constraints
        self.constraints = None
        data = asdict(self)
        self.constraints = constraints
        data["intent"] = self.intent.value
        data["constraints"] = constraints.as_dict() if constraints is not None else None
        return data
