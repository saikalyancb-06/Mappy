"""Feedback → vibe profiles → personalised ranking, including sparse, conflicting and negative feedback."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.text import normalize
from app.db.models import Feedback, FeedbackAspect, FeedbackVibe, PlaceAspectSignal, PlaceFeedbackSummary, PlaceVibeProfile, Poi, UserAspectPreference, UserVibePreference, Vibe
from app.db.session import SessionLocal
from app.feedback import aggregate
from app.feedback.bootstrap import load_bootstrap
from app.feedback.nlp import analyse
from app.feedback.service import FeedbackError, FeedbackInput, parse_input, submit_feedback
from app.feedback.signals import community_quality, place_communities, user_vibes, vibe_compatibility
from app.feedback.vocabulary import InvalidCustomVibe, normalize_custom_vibe, public_vocabulary, sync_vocabulary
from app.geo.spatial import get_pois
from app.main import app
from app.ranking.ranker import RankRequest, rank

# Places at the same distance from the test point, so only community/vibe signals differ.
PLACES = [
    ("fv-garden", "Quiet Garden", "park", 10.010, 20.000, "[]", 0),
    ("fv-deck", "Party Deck", "nightlife", 10.000, 20.010, "[]", 0),
    ("fv-bazaar", "Busy Bazaar", "market", 9.990, 20.000, "[]", 0),
    ("fv-corner", "Calm Corner", "market", 10.000, 19.990, "[]", 0),
    ("fv-lounge", "Sky Lounge", "restaurant", 10.007, 20.007, "[]", 3),
    ("fv-diner", "Street Diner", "restaurant", 9.993, 19.993, "[]", 1),
    ("fv-new", "Brand New Viewpoint", "viewpoint", 10.005, 20.005, '["sunset", "peaceful"]', 0),
]


@pytest.fixture(autouse=True)
def feedback_world():
    sync_vocabulary()
    with SessionLocal() as db:
        for pid, name, category, lat, lon, tags, price in PLACES:
            db.merge(Poi(id=pid, destination_id="dest-testville", name=name, normalized_name=normalize(name), kind="attraction", category=category, tags=tags, lat=lat, lon=lon, price_level=price, opening_hours='{"always_open": true}'))
        db.commit()
    yield
    with SessionLocal() as db:
        for model in (FeedbackVibe, FeedbackAspect):
            db.execute(delete(model))
        for model in (Feedback, PlaceVibeProfile, PlaceAspectSignal, PlaceFeedbackSummary, UserVibePreference, UserAspectPreference):
            db.execute(delete(model))
        db.execute(delete(Vibe).where(Vibe.origin == "custom"))
        db.execute(delete(Poi).where(Poi.id.in_([p[0] for p in PLACES])))
        db.commit()
    from app.feedback.vocabulary import invalidate_vocabulary

    invalidate_vocabulary()


def give(user, place, rating, vibes=(), liked=(), disliked=(), text=None, custom=()):
    return submit_feedback(FeedbackInput(place_id=place, overall_rating=rating, vibes=list(vibes), custom_vibes=list(custom), liked_aspects=list(liked), disliked_aspects=list(disliked), text_feedback=text), user)


def crowd(place, n, rating, vibes=(), disliked=()):
    for i in range(n):
        give(f"crowd-{place}-{i}", place, rating, vibes, disliked=disliked)


def _rank(user_id, ids, profile_name="discovery"):
    candidates = get_pois(ids, (10.0, 20.0))
    return [c.id for c in rank(candidates, RankRequest(profile_name=profile_name, reference=(10.0, 20.0), reference_label="Testville centre", radius_km=5, user={"user_id": user_id})).ranked]


# ---- scenarios -------------------------------------------------------------------------------

def test_peaceful_scenic_traveller_vs_lively_youthful_traveller():
    crowd("fv-garden", 6, 4, ["peaceful", "scenic", "calm"])
    crowd("fv-deck", 6, 4, ["lively", "youthful", "nightlife"])
    for place in ("tv-sunset-rock", "tv-lotus-temple"):
        give("quiet-user", place, 5, ["peaceful", "scenic"])
    for place in ("tv-old-fort", "tv-sunrise-cafe-market"):
        give("party-user", place, 5, ["lively", "youthful"])
    assert _rank("quiet-user", ["fv-garden", "fv-deck"]) == ["fv-garden", "fv-deck"]
    assert _rank("party-user", ["fv-garden", "fv-deck"]) == ["fv-deck", "fv-garden"]
    quiet = user_vibes("quiet-user")
    assert quiet.affinity["peaceful"] > 0.5 and quiet.status == "emerging"


def test_same_geography_different_vibe_compatibility_changes_order_and_explains_it():
    crowd("fv-garden", 6, 4, ["peaceful", "scenic"])
    crowd("fv-deck", 6, 4, ["nightlife", "lively"])
    for place in ("tv-sunset-rock", "tv-lotus-temple", "tv-far-lake"):
        give("nature-fan", place, 5, ["peaceful", "scenic"])
    candidates = {c.id: c for c in rank(get_pois(["fv-garden", "fv-deck"], (10.0, 20.0)), RankRequest(reference=(10.0, 20.0), reference_label="Testville centre", radius_km=5, user={"user_id": "nature-fan"})).ranked}
    assert candidates["fv-garden"].scores["vibe"] > 0.5 > candidates["fv-deck"].scores["vibe"] - 0.01
    assert abs(candidates["fv-garden"].scores["geographic"] - candidates["fv-deck"].scores["geographic"]) < 0.01
    assert any("Matches vibes you've enjoyed" in r for r in candidates["fv-garden"].reasons)


def test_traveller_who_dislikes_crowds_gets_crowded_places_lower():
    crowd("fv-bazaar", 5, 4, ["lively", "shopping"], disliked=["too_crowded"])
    crowd("fv-corner", 5, 4, ["lively", "shopping"])
    for place in ("tv-old-fort", "tv-museum", "tv-lotus-temple"):
        give("crowd-hater", place, 2, disliked=["too_crowded"])
    assert user_vibes("crowd-hater").aversion["too_crowded"] > 0.3
    assert _rank("crowd-hater", ["fv-bazaar", "fv-corner"]) == ["fv-corner", "fv-bazaar"]
    bazaar = next(c for c in rank(get_pois(["fv-bazaar"], (10.0, 20.0)), RankRequest(reference=(10.0, 20.0), radius_km=5, user={"user_id": "crowd-hater"})).ranked)
    assert any("too crowded" in r for r in bazaar.reasons)


def test_traveller_who_dislikes_expensive_places():
    for place in ("tv-old-fort", "tv-museum"):
        give("thrifty", place, 2, disliked=["too_expensive"])
    communities = place_communities(get_pois(["fv-lounge", "fv-diner"]))
    assert communities["fv-lounge"].aspects.get("too_expensive", 0) > 0  # prior from its price level, before any feedback
    assert _rank("thrifty", ["fv-lounge", "fv-diner"]) == ["fv-diner", "fv-lounge"]


def test_no_history_is_neutral_and_one_review_moves_little():
    cold = user_vibes("nobody")
    assert cold.status == "cold_start" and cold.affinity == {}
    community = place_communities(get_pois(["fv-garden"]))["fv-garden"]
    assert vibe_compatibility(community, cold)[0] == 0.5
    give("one-timer", "tv-sunset-rock", 5, ["scenic"])
    single = user_vibes("one-timer")
    assert single.status == "emerging" and 0.5 < single.affinity["scenic"] < 0.75


def test_conflicting_feedback_is_averaged_and_one_review_cannot_redefine_a_place():
    crowd("fv-garden", 8, 5, ["peaceful", "scenic"])
    before = place_communities(get_pois(["fv-garden"]))["fv-garden"]
    give("contrarian", "fv-garden", 1, ["lively", "nightlife"], disliked=["too_noisy"])
    after = place_communities(get_pois(["fv-garden"]))["fv-garden"]
    assert after.vibes["peaceful"] > 0.6 and after.vibes["nightlife"] < 0.2
    assert before.bayes_rating - after.bayes_rating < 0.4 and after.feedback_count == 9
    mixed_place = "fv-deck"
    for i, (rating, vibes) in enumerate([(5, ["lively"]), (1, ["lively"]), (4, ["social"]), (2, ["lively"])]):
        give(f"mixed-{i}", mixed_place, rating, vibes)
    summary = place_communities(get_pois([mixed_place]))[mixed_place]
    assert 2.5 < summary.bayes_rating < 3.9 and summary.confidence < 0.5


def test_new_place_without_feedback_uses_its_own_tags_at_zero_confidence():
    community = place_communities(get_pois(["fv-new"]))["fv-new"]
    assert not community.from_feedback and community.confidence == 0.0
    assert community.vibes["peaceful"] >= 0.5 and community.vibes["nightlife"] < 0.2
    assert community_quality(community) == (0.5, [])


def test_new_vibe_can_join_the_vocabulary():
    assert normalize_custom_vibe("chill") == ("calm", "chill")  # synonym → existing vibe
    for i in range(5):
        give(f"artsy-{i}", "fv-garden", 4, custom=["Artsy"])
    keys = [v["key"] for v in public_vocabulary()["vibes"]]
    assert "artsy" in keys  # promoted after enough travellers used it — no schema change
    aggregate.recompute_place("fv-garden")
    assert place_communities(get_pois(["fv-garden"]))["fv-garden"].vibes["artsy"] > 0.4


@pytest.mark.parametrize("text", ["", "12345", "a" * 40, "x!", "fuck this"])
def test_malformed_custom_vibes_are_rejected(text):
    with pytest.raises(InvalidCustomVibe):
        normalize_custom_vibe(text)


def test_user_dislike_is_not_a_global_penalty():
    crowd("fv-deck", 6, 5, ["nightlife", "lively"])
    before = place_communities(get_pois(["fv-deck"]))["fv-deck"]
    fan_before = _rank("fan", ["fv-deck", "fv-garden"])
    give("hater", "fv-deck", 1, ["nightlife"], disliked=["too_noisy"])
    after = place_communities(get_pois(["fv-deck"]))["fv-deck"]
    assert user_vibes("hater").affinity["nightlife"] < 0.5  # the hater's own profile changes…
    assert community_quality(after)[0] > 0.5 and before.bayes_rating - after.bayes_rating < 0.35  # …the place stays well rated overall
    assert _rank("fan", ["fv-deck", "fv-garden"]) == fan_before  # and other travellers are unaffected


def test_text_is_kept_and_derived_signals_never_override_selections():
    saved = give("writer", "fv-garden", 4, ["scenic"], text="So peaceful but it was crowded at noon")
    assert saved["text_feedback"] == "So peaceful but it was crowded at noon"
    assert saved["vibes"] == ["scenic"] and "peaceful" in saved["derived_vibes"]
    assert saved["derived_vibe_scores"] == {"scenic": 1.0, "peaceful": 0.5}
    assert "too_crowded" in saved["derived_aspects"] and saved["disliked_aspects"] == []
    assert saved["sentiment"] in {"mixed", "positive"}


def test_nlp_sentiment_negation_and_aspects():
    assert analyse("Not crowded at all, clean and worth it", 4).liked_aspects == ["good_value", "clean", "less_crowded"]
    assert analyse("Overpriced and dirty. Avoid.", 1).sentiment == "negative"
    assert analyse("", 5).sentiment == "positive" and analyse("", 5).sentiment_source == "rating"


def test_validation_errors():
    with pytest.raises(FeedbackError):
        parse_input({"place_id": "fv-garden", "overall_rating": 7})
    with pytest.raises(FeedbackError):
        parse_input({"place_id": "fv-garden", "overall_rating": 4, "crowd_level": "insane"})
    with pytest.raises(FeedbackError):
        submit_feedback(parse_input({"place_id": "fv-garden", "overall_rating": 4, "vibes": ["spooky"]}), None)
    with pytest.raises(FeedbackError):
        submit_feedback(parse_input({"place_id": "fv-garden", "overall_rating": 4, "disliked_aspects": ["good_views"]}), None)


def test_api_flow(tmp_path):
    with TestClient(app) as client:
        vocabulary = client.get("/api/feedback/vocabulary").json()
        assert len(vocabulary["vibes"]) >= 15 and vocabulary["ratings"][0] == {"value": 5, "recommendation": "loved_it", "label": "Loved it"}
        token = client.post("/api/auth/signup", json={"name": "V", "email": "vibes@example.com", "password": "secret123"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/me/vibes").status_code == 401
        response = client.post("/api/feedback", headers=headers, json={"place_id": "fv-garden", "overall_rating": 5, "vibes": ["peaceful", "scenic"], "custom_vibes": ["dreamy"], "liked_aspects": ["good_views"], "text_feedback": "Lovely and quiet."})
        body = response.json()
        assert response.status_code == 200 and body["feedback"]["recommendation"] == "loved_it" and body["community"]["kind"] == "community_signal"
        assert [v["key"] for v in body["your_vibes"]["liked_vibes"]][:2] and body["your_vibes"]["status"] == "emerging"
        assert client.post("/api/feedback", json={"place_id": "fv-garden", "overall_rating": 3}).status_code == 200  # anonymous feedback counts for the place
        bad = client.post("/api/feedback", json={"place_id": "fv-garden", "overall_rating": 3, "custom_vibes": ["123"]})
        assert bad.status_code == 422 and bad.json()["detail"]["field"] == "custom_vibes"
        community = client.get("/api/places/fv-garden/community").json()
        assert community["feedback_count"] == 2 and "opinions, not verified facts" in community["note"] and community["recent"][0]["text"] == "Lovely and quiet."
        mine = client.get("/api/me/vibes", headers=headers).json()
        assert "vibe_preferences" not in mine  # travellers see labels, not internal scores
        stats = client.get("/api/feedback/analytics", params={"destination_id": "dest-testville"}).json()
        assert stats["volume"]["feedback"] == 2 and stats["most_selected_vibes"] and "recommendation_changes" in stats


def test_synthetic_bootstrap_loads_through_the_pipeline_and_can_be_excluded(tmp_path, monkeypatch):
    path = tmp_path / "feedback.jsonl"
    rows = [
        {"feedback_id": "FBT1", "user_id": "synthetic-user-001", "place_id": "fv-garden", "overall_rating": 5, "vibes": ["peaceful"], "visit_date": "2026-09-01", "is_synthetic": True},
        {"feedback_id": "FBT2", "user_id": "synthetic-user-002", "place_id": "unknown-place", "overall_rating": 4, "vibes": [], "visit_date": "2026-09-01", "is_synthetic": True},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    counts = load_bootstrap(path)
    assert counts["loaded"] == 1 and counts["skipped_unknown_place"] == 1
    with SessionLocal() as db:
        assert db.scalars(select(Feedback).where(Feedback.id == "FBT1")).first().is_synthetic
    garden = place_communities(get_pois(["fv-garden"]))["fv-garden"]
    assert garden.synthetic_share == 1.0 and "synthetic" in garden.as_dict()["note"]
    monkeypatch.setattr(aggregate, "FEEDBACK_INCLUDE_SYNTHETIC", False)
    aggregate.recompute_place("fv-garden")
    assert not place_communities(get_pois(["fv-garden"]))["fv-garden"].from_feedback
