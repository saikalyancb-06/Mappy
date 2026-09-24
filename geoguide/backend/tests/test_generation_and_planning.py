"""Grounded generation, answer validation and itinerary optimisation."""
from datetime import datetime, timezone

from app.geo.spatial import SpatialQuery, nearby
from app.llm.client import LLMClient, LLMUnavailable
from app.llm.evidence import EvidenceBuilder
from app.llm.generator import generate
from app.llm.prompts import AnswerContext
from app.llm.validator import validate
from app.planning.itinerary import PlanRequest, optimise


def _evidence():
    builder = EvidenceBuilder()
    candidates = nearby(SpatialQuery(lat=10.0, lon=20.0, radius_km=1.0, categories=["monument", "temple"]))
    fort = next(c for c in candidates if c.id == "tv-old-fort")
    fort.rating, fort.review_count = 4.3, 120
    builder.add_candidate(fort, "Testville centre")
    builder.add("knowledge", "History of Testville", "Testville was founded as a river trading post in 1450.")
    return builder.items


class ScriptedLLM(LLMClient):
    def __init__(self, replies):
        super().__init__(api_key="test")
        self.replies = list(replies)
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_validator_accepts_grounded_answer():
    evidence = _evidence()
    answer = "**Old Fort** is a ruined hilltop fort rated 4.3 [E1]. Entry is ₹50 [E1] and it is open 08:00–18:00 [E1]. The town dates from 1450 [E2]."
    assert validate(answer, evidence).status == "ok"


def test_validator_flags_invented_facts():
    evidence = _evidence()
    answer = "**Old Fort** is rated 4.9 [E1], 7.5 km away [E1], costs ₹500 [E1], opens at 05:00 [E1], it is 41°C [E1]. Try **Imaginary Palace** [E9]. It is open now."
    kinds = {issue["type"] for issue in validate(answer, evidence).issues}
    assert kinds >= {"invented_rating", "invented_distance", "invented_price", "invented_time", "invented_temperature", "unsupported_place", "invalid_citation"}


def test_generation_repairs_then_falls_back_to_evidence():
    evidence = _evidence()
    context = AnswerContext(question="Tell me about the fort", intent="PLACE_LOOKUP", location_notes=[], evidence=evidence)
    llm = ScriptedLLM(["**Old Fort** is rated 4.9 [E1].", "**Old Fort** has 5 stars [E1]. Also see **Fake Tower**."])
    answer = generate(context, client=llm)
    assert answer.mode == "deterministic" and len(llm.prompts) == 2
    assert "PROBLEMS" in llm.prompts[1][1]["content"]  # repair attempt got validator feedback
    assert "[E1]" in answer.text and "4.9" not in answer.text

    llm_ok = ScriptedLLM(["The **Old Fort** is a ruined hilltop fort [E1]."])
    assert generate(context, client=llm_ok).mode == "llm"


def test_llm_outage_still_answers_from_evidence():
    context = AnswerContext(question="history?", intent="DESTINATION_KNOWLEDGE", location_notes=[], evidence=_evidence())
    answer = generate(context, client=ScriptedLLM([LLMUnavailable("timeout", "timed out")]))
    assert answer.mode == "deterministic" and "1450" in answer.text
    assert answer.errors[0]["source"] == "groq"


def test_no_evidence_never_calls_the_llm():
    llm = ScriptedLLM([AssertionError("must not be called")])
    answer = generate(AnswerContext(question="x", intent="NEARBY_SEARCH", location_notes=[], evidence=[], extra={"empty_message": "Nothing verified."}), client=llm)
    assert answer.text == "Nothing verified." and not llm.prompts


def _plan(preset="balanced", duration=240, start_hour=9, walking="moderate", locked=None, previous=None, weekday_day=5):
    candidates = nearby(SpatialQuery(lat=10.0, lon=20.0, radius_km=8.0, kinds=["attraction", "activity"]))
    for index, candidate in enumerate(candidates):
        candidate.score = 0.9 - index * 0.05
    start = datetime(2026, 1, weekday_day, start_hour, 0, tzinfo=timezone.utc)
    return optimise(PlanRequest(candidates=candidates, start_point=(10.0, 20.0), start_label="Testville", start_time=start, duration_min=duration, preset=preset, walking=walking, currency="INR", locked_ids=locked or [], previous=previous))


def test_plan_fits_window_and_respects_opening_hours():
    plan = _plan(duration=180)
    assert plan["stops"] and plan["used_min"] <= 180
    for stop in plan["stops"]:
        assert stop["open_check"] in {"verified_open", "hours_unknown"}
    # Museum is closed on Fridays (2026-01-09 is a Friday) and must never be scheduled then.
    friday = _plan(duration=540, weekday_day=9)
    assert "tv-museum" not in [s["poi_id"] for s in friday["stops"]]


def test_temple_closed_midday_is_not_scheduled_then():
    plan = _plan(duration=120, start_hour=12)
    for stop in plan["stops"]:
        if stop["poi_id"] == "tv-lotus-temple":
            assert stop["arrive"] >= "16:00" or stop["depart"] <= "12:00"


def test_locked_stop_is_kept():
    plan = _plan(locked=["tv-sunset-rock"], duration=240)
    assert "tv-sunset-rock" in [s["poi_id"] for s in plan["stops"]]


def test_replan_presets_move_in_the_expected_direction():
    balanced = _plan(duration=540, walking="high")
    cheaper = _plan(preset="cheaper", duration=540, walking="high", previous=balanced)
    greener = _plan(preset="greener", duration=540, walking="high", previous=balanced)
    less_walking = _plan(preset="less_walking", duration=540, walking="high", previous=balanced)
    assert cheaper["totals"]["cost"] <= balanced["totals"]["cost"]
    assert greener["totals"]["co2_g"] <= balanced["totals"]["co2_g"]
    assert less_walking["totals"]["walking_km"] <= balanced["totals"]["walking_km"]
    assert cheaper["explanation"][0].startswith("Re-planned for cheaper")
