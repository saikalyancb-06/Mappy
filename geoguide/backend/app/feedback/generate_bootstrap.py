"""Generate the SYNTHETIC bootstrap feedback dataset (development/testing only).

    python -m app.feedback.generate_bootstrap --count 800 --seed 13

Every record is marked ``is_synthetic: true`` / ``source: synthetic_bootstrap`` and uses
synthetic user ids. It is not real user-generated data: it exists so aggregation and
personalisation can be developed and tested before real feedback accumulates, and real
feedback gradually replaces it (set FEEDBACK_INCLUDE_SYNTHETIC=false to exclude it).

Model: each place gets a latent character from its dataset category/tags (plus noise,
a latent quality, crowding and price); each synthetic traveller gets a persona (liked
vibes, disliked characteristics). Ratings follow match + quality + noise, so places are
not universally liked; some places are polarising; negative feedback is explicit.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, FEEDBACK_BOOTSTRAP_PATH, PS13_DB_PATH
from app.core.rules import load_rules

VIBES = ["peaceful", "aesthetic", "lively", "calm", "romantic", "family_friendly", "social", "youthful", "cozy", "cultural", "scenic", "adventurous", "premium", "shopping", "nightlife"]

# Latent vibe character per organiser category code (development model, not facts).
CODE_VIBES = {
    "adventure": {"adventurous": 0.9, "scenic": 0.5, "youthful": 0.4, "social": 0.3},
    "beach_active": {"lively": 0.7, "social": 0.6, "youthful": 0.5, "scenic": 0.5},
    "beach_quiet": {"peaceful": 0.8, "scenic": 0.7, "calm": 0.7, "romantic": 0.5},
    "food_cooking": {"cozy": 0.6, "cultural": 0.5, "social": 0.4},
    "food_fine": {"premium": 0.8, "romantic": 0.6, "cozy": 0.4, "aesthetic": 0.5},
    "food_market": {"lively": 0.7, "social": 0.5, "shopping": 0.5},
    "food_street": {"lively": 0.7, "social": 0.5, "youthful": 0.4},
    "heritage": {"cultural": 0.9, "aesthetic": 0.5, "scenic": 0.3},
    "museum": {"cultural": 0.8, "calm": 0.5, "family_friendly": 0.4},
    "museum_science": {"family_friendly": 0.8, "cultural": 0.5},
    "nature": {"scenic": 0.8, "peaceful": 0.6, "calm": 0.5},
    "nature_garden": {"peaceful": 0.8, "family_friendly": 0.6, "calm": 0.6, "scenic": 0.5},
    "nightlife": {"nightlife": 0.9, "lively": 0.7, "social": 0.7, "youthful": 0.6},
    "religious": {"cultural": 0.8, "calm": 0.5, "peaceful": 0.4},
    "shopping_bazaar": {"shopping": 0.8, "lively": 0.7, "cultural": 0.3},
    "shopping_craft": {"shopping": 0.7, "cultural": 0.5, "aesthetic": 0.4},
    "shopping_mall": {"shopping": 0.8, "family_friendly": 0.5, "youthful": 0.4, "premium": 0.3},
    "wellness": {"calm": 0.9, "peaceful": 0.8, "premium": 0.3},
    "wildlife": {"family_friendly": 0.7, "scenic": 0.6, "adventurous": 0.4},
    # curated categories
    "temple": {"cultural": 0.8, "calm": 0.5, "peaceful": 0.4},
    "monument": {"cultural": 0.9, "aesthetic": 0.5},
    "viewpoint": {"scenic": 0.9, "peaceful": 0.5, "romantic": 0.4, "aesthetic": 0.5},
    "lake": {"scenic": 0.7, "peaceful": 0.6, "calm": 0.5},
    "market": {"shopping": 0.7, "lively": 0.7},
    "restaurant": {"cozy": 0.5, "social": 0.5},
    "activity": {"adventurous": 0.7, "social": 0.4},
    "village": {"cultural": 0.7, "peaceful": 0.6},
}

PERSONAS: list[dict[str, Any]] = [
    {"name": "quiet nature lover", "likes": {"peaceful": 1, "scenic": 1, "calm": 0.8}, "dislikes": {"lively": 0.6, "nightlife": 0.8}, "averse": ["too_crowded", "too_noisy"]},
    {"name": "night owl", "likes": {"nightlife": 1, "lively": 1, "youthful": 0.8, "social": 0.8}, "dislikes": {"calm": 0.5, "cultural": 0.3}, "averse": ["boring"]},
    {"name": "history buff", "likes": {"cultural": 1, "aesthetic": 0.6, "calm": 0.4}, "dislikes": {"nightlife": 0.5}, "averse": ["poor_maintenance", "not_as_described"]},
    {"name": "family with kids", "likes": {"family_friendly": 1, "peaceful": 0.5, "scenic": 0.4}, "dislikes": {"nightlife": 1, "adventurous": 0.3}, "averse": ["too_crowded", "uncomfortable", "difficult_to_reach"]},
    {"name": "budget backpacker", "likes": {"adventurous": 0.8, "social": 0.7, "youthful": 0.6, "lively": 0.5}, "dislikes": {"premium": 0.8}, "averse": ["too_expensive"]},
    {"name": "couple", "likes": {"romantic": 1, "scenic": 0.8, "cozy": 0.6, "premium": 0.4}, "dislikes": {"lively": 0.4}, "averse": ["too_crowded", "too_noisy"]},
    {"name": "shopper", "likes": {"shopping": 1, "lively": 0.6, "social": 0.4}, "dislikes": {"adventurous": 0.4}, "averse": ["too_expensive", "poor_service"]},
    {"name": "luxury traveller", "likes": {"premium": 1, "aesthetic": 0.7, "romantic": 0.5, "calm": 0.4}, "dislikes": {"lively": 0.3}, "averse": ["poor_service", "poor_maintenance", "too_crowded"]},
    {"name": "adventurer", "likes": {"adventurous": 1, "scenic": 0.7}, "dislikes": {"shopping": 0.5}, "averse": ["boring", "nothing_special"]},
    {"name": "cafe hopper", "likes": {"cozy": 1, "aesthetic": 0.7, "calm": 0.5, "social": 0.4}, "dislikes": {"adventurous": 0.3}, "averse": ["too_noisy", "poor_service"]},
]

LIKED = {"scenic": "good_views", "aesthetic": "good_for_photos", "cultural": "interesting_history", "cozy": "good_ambience", "romantic": "good_ambience", "adventurous": "activities", "family_friendly": "activities", "premium": "good_service", "peaceful": "less_crowded", "calm": "less_crowded"}
POSITIVE_TEXT = {
    "peaceful": ["Very peaceful place to walk and relax.", "Quiet and relaxing, exactly what we needed."],
    "scenic": ["The views were stunning, especially near sunset.", "Beautiful landscape all around."],
    "cultural": ["Fascinating history, worth hiring a guide.", "Loved the heritage and the old architecture."],
    "lively": ["Buzzing and fun, lots going on.", "Great energy in the evening."],
    "nightlife": ["Good drinks and a fun crowd at night.", "The live music was great."],
    "family_friendly": ["Our kids loved it.", "Very family friendly, lots of space for children."],
    "cozy": ["Cozy and warm, perfect for a slow afternoon.", "Such a cosy little spot."],
    "adventurous": ["What a thrill, the trek was amazing.", "Great adventure, well organised."],
    "premium": ["Upscale and elegant, excellent service.", "Classy place, felt special."],
    "shopping": ["Great for shopping and bargaining.", "Lots of stalls and good finds."],
    "romantic": ["Lovely for a date.", "Romantic setting, we enjoyed it as a couple."],
    "calm": ["Calm and soothing.", "Slow-paced and chill."],
    "aesthetic": ["So pretty, great for photos.", "Picturesque everywhere you look."],
    "social": ["Good place to hang out with friends.", "Easy to meet people here."],
    "youthful": ["Young, trendy crowd.", "Very hip, lots of students."],
}
NEGATIVE_TEXT = {
    "too_crowded": ["It was extremely crowded.", "Packed with people, long queues."],
    "too_expensive": ["Overpriced for what it is.", "Too expensive."],
    "poor_service": ["Staff were rude.", "Slow service."],
    "boring": ["Honestly a bit boring.", "Nothing to do after twenty minutes."],
    "not_as_described": ["Not as described online.", "Looked nothing like the photos."],
    "difficult_to_reach": ["Hard to reach without a car.", "Difficult to get to, bad road."],
    "poor_maintenance": ["Dirty and poorly maintained.", "Litter everywhere."],
    "uncomfortable": ["Felt uncomfortable, no shade at all.", "Felt unsafe after dark."],
    "too_noisy": ["Very noisy.", "Too loud to talk."],
    "nothing_special": ["Nothing special, overrated.", "Pretty average."],
}


def _places(rng: random.Random, per_code: int) -> list[dict]:
    places: list[dict] = []
    mapping = json.loads((DATA_DIR / "config" / "ps13_mapping.json").read_text())
    if PS13_DB_PATH.exists():
        db = sqlite3.connect(PS13_DB_PATH)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT a.poi_id, a.name, a.tags, a.entry_cost, a.popularity_score, c.code, ci.name AS city FROM activities_poi a JOIN categories c USING(category_id) JOIN cities ci USING(city_id) WHERE a.status = 'active'").fetchall()
        by_code: dict[str, list] = {}
        for row in rows:
            by_code.setdefault(row["code"], []).append(row)
        for code, items in sorted(by_code.items()):
            for row in rng.sample(items, min(per_code, len(items))):
                category = mapping["category_codes"].get(code) or mapping["category_codes"].get(code.split("_")[0], "activity")
                places.append({"place_id": row["poi_id"], "place_name": row["name"], "city": row["city"], "category": category, "code": code, "tags": (row["tags"] or "").split(","), "price": float(row["entry_cost"] or 0), "popularity": row["popularity_score"] or 50})
    pack = DATA_DIR / "packs" / "hampi" / "pois.json"
    if pack.exists():
        for poi in json.loads(pack.read_text()):
            if poi.get("kind") in {"attraction", "food"} and poi.get("category") != "hotel":
                places.append({"place_id": poi["id"], "place_name": poi["name"], "city": "Hampi", "category": poi["category"], "code": poi["category"], "tags": poi.get("tags") or [], "price": float(poi.get("entry_fee") or 0), "popularity": 80 if "iconic" in (poi.get("tags") or []) else 50})
    return places


def _character(place: dict, rng: random.Random) -> dict[str, float]:
    code = place["code"]
    base = CODE_VIBES.get(code) or CODE_VIBES.get(code.split("_")[0]) or CODE_VIBES.get(place["category"], {})
    character = {v: max(0.0, min(1.0, base.get(v, 0.05) + rng.gauss(0, 0.12))) for v in VIBES}
    tags = set(place["tags"])
    for entry in load_rules("vibes")["vibes"]:
        if tags & set(entry.get("place_tags", [])):
            character[entry["key"]] = min(1.0, character[entry["key"]] + 0.25)
    return character


def generate(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    places = _places(rng, per_code=4)
    for place in places:
        place["character"] = _character(place, rng)
        place["quality"] = max(1.8, min(4.8, rng.gauss(3.7, 0.55)))
        place["crowded"] = "crowded" in place["tags"] or place["popularity"] >= 75 or rng.random() < 0.15
        place["polarising"] = rng.random() < 0.1
    users: list[dict[str, Any]] = []
    for index in range(60):
        persona = PERSONAS[index % len(PERSONAS)]
        users.append({"user_id": f"synthetic-user-{index + 1:03d}", "persona": persona})
    records = []
    today = date(2026, 9, 1)
    for number in range(1, count + 1):
        user = rng.choice(users)
        persona = user["persona"]
        # Travellers mostly visit places that suit them, sometimes not (that's where negative feedback comes from).
        weights = [0.3 + sum(persona["likes"].get(v, 0) * p["character"][v] for v in VIBES) for p in places]
        place = rng.choices(places, weights=weights)[0] if rng.random() < 0.75 else rng.choice(places)
        ch = place["character"]
        match = sum(persona["likes"].get(v, 0) * ch[v] for v in VIBES) / max(sum(persona["likes"].values()), 1) - sum(w * ch[v] for v, w in persona["dislikes"].items()) / max(sum(persona["dislikes"].values()), 1) * 0.8
        disliked = []
        if place["crowded"] and ("too_crowded" in persona["averse"] or rng.random() < 0.3):
            disliked.append("too_crowded")
        if place["price"] >= 800 and ("too_expensive" in persona["averse"] or rng.random() < 0.2):
            disliked.append("too_expensive")
        score = place["quality"] + 1.6 * match - 0.3 - 0.5 * len(disliked) + rng.gauss(0, 0.6)
        if place["polarising"]:
            score += rng.choice([-1.4, 1.2])
        rating = int(max(1, min(5, round(score))))
        if rating <= 2 and rng.random() < 0.8:
            disliked += rng.sample([k for k in NEGATIVE_TEXT if k not in disliked], k=rng.choice([1, 1, 2]))
        disliked = list(dict.fromkeys(disliked))
        perceived = sorted(VIBES, key=lambda v: -(ch[v] + rng.gauss(0, 0.15) + 0.1 * persona["likes"].get(v, 0)))
        vibes = [v for v in perceived[:4] if ch[v] > 0.35][: rng.choice([1, 2, 2, 3, 3, 4])] or perceived[:1]
        liked = list(dict.fromkeys(LIKED[v] for v in vibes if v in LIKED and rating >= 3 and rng.random() < 0.6))
        if "too_crowded" in disliked and "less_crowded" in liked:
            liked.remove("less_crowded")
        if place["price"] == 0 and rating >= 4 and rng.random() < 0.4:
            liked.append("good_value")
        crowd = rng.choice(["high", "very_high"]) if place["crowded"] else rng.choice(["empty", "low", "moderate", "moderate"])
        price = "free" if place["price"] == 0 else "cheap" if place["price"] < 300 else "moderate" if place["price"] < 800 else rng.choice(["expensive", "very_expensive"])
        text = None
        if rng.random() < 0.6:
            parts = [rng.choice(POSITIVE_TEXT[v]) for v in vibes[: rng.choice([1, 2])] if v in POSITIVE_TEXT] if rating >= 3 else []
            parts += [rng.choice(NEGATIVE_TEXT[a]) for a in disliked[:2]]
            if rating == 3 and not disliked:
                parts.append(rng.choice(["It was okay.", "Fine for an hour.", "Decent, not amazing."]))
            if rng.random() < 0.3:
                parts.append(rng.choice(["Go early in the morning.", "Carry water.", "Weekdays are better.", "Would come back with friends."]))
            text = " ".join(parts) or None
        scores = {}
        if rng.random() < 0.5:
            for field in ["ambience_score", "cleanliness_score", "service_score", "accessibility_score", "photo_worthiness"]:
                if rng.random() < 0.6:
                    scores[field] = int(max(1, min(5, round(rating + rng.gauss(0, 0.8)))))
        records.append({
            "feedback_id": f"FB{number:06d}", "user_id": user["user_id"], "place_id": place["place_id"], "place_name": place["place_name"],
            "city": place["city"], "category": place["category"], "visit_date": (today - timedelta(days=rng.randint(0, 540))).isoformat(),
            "overall_rating": rating, "recommendation": load_rules("vibes")["recommendation"][str(rating)],
            "vibes": vibes, "liked_aspects": liked, "disliked_aspects": disliked, "crowd_level": crowd, "price_level": price,
            **scores, "text_feedback": text, "is_synthetic": True, "source": "synthetic_bootstrap", "persona": persona["name"],
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=800)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--out", type=Path, default=FEEDBACK_BOOTSTRAP_PATH)
    args = parser.parse_args()
    records = generate(args.count, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} synthetic records to {args.out}")


if __name__ == "__main__":
    main()
