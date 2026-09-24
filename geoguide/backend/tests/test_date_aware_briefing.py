"""Integration tests verifying date-aware briefing recomputation,
source attribution, weather basis, season framing, and honest empty event reporting.
"""
from datetime import date
from pathlib import Path
from app.context.engine import build_city_context
from app.geo.city import city_for_destination
from app.db.seed import load_pack
from tests.conftest import HAMPI_PACK


def _ensure_hampi():
    import sqlite3
    from app.config import DATA_DIR
    from app.db.session import engine

    city = city_for_destination("dest-hampi")
    if city is None and HAMPI_PACK.exists():
        load_pack(HAMPI_PACK)
        city = city_for_destination("dest-hampi")

    # If test DB lacks dataset events/weather from main geoguide.db, copy them
    src_db = Path(__file__).resolve().parent.parent / "data" / "geoguide.db"
    test_db_path = Path(engine.url.database) if engine.url.database else None
    if src_db.exists() and test_db_path and test_db_path.exists():
        con_src = sqlite3.connect(src_db)
        con_dest = sqlite3.connect(test_db_path)
        cur_src = con_src.cursor()
        cur_dest = con_dest.cursor()

        # Ensure events_festivals has the dataset rows (not just the 3 undated pack events)
        cur_src.execute("SELECT * FROM events_festivals WHERE destination_id = 'dest-hampi'")
        rows = cur_src.fetchall()
        if rows:
            col_names = [desc[0] for desc in cur_src.description]
            placeholders = ",".join(["?"] * len(col_names))
            cur_dest.executemany(f"INSERT OR REPLACE INTO events_festivals ({','.join(col_names)}) VALUES ({placeholders})", rows)
            con_dest.commit()

        # Ensure weather_daily has the dataset rows
        cur_src.execute("SELECT * FROM weather_daily WHERE destination_id = 'dest-hampi'")
        rows = cur_src.fetchall()
        if rows:
            col_names = [desc[0] for desc in cur_src.description]
            placeholders = ",".join(["?"] * len(col_names))
            cur_dest.executemany(f"INSERT OR REPLACE INTO weather_daily ({','.join(col_names)}) VALUES ({placeholders})", rows)
            con_dest.commit()

        con_src.close()
        con_dest.close()

    return city


# Ensure tables and pack are loaded at module load time
_ensure_hampi()


def test_festival_week_recomputation():
    """When moved to a festival week (e.g. 2026-09-25 for Hampi),

    events, tips, weather and sources recompute accordingly.
    """
    city = _ensure_hampi()
    assert city is not None

    res = build_city_context(city, date(2026, 9, 25), briefing_mode="deferred")
    print("DEBUG TEST FESTIVAL EVENTS:", res["events"])
    print("DEBUG PROVIDER ERRORS:", res.get("provider_errors"))

    # Date info
    assert res["date"]["selected"] == "2026-09-25"
    assert res["season"]["name"] == "monsoon"

    # Events present from dataset
    events = res["events"]["events"]
    assert len(events) >= 1
    event_names = [e["name"] for e in events]
    assert "Hampi Food Week" in event_names

    # Weather check
    assert res["weather"]["status"] == "ok"
    assert res["weather"]["basis"] in {"forecast", "dataset"}
    assert "signals" in res["weather"]

    # Tips recompute from underlying data & cite basis
    assert len(res["tips"]) > 0
    assert any(t["kind"] == "event" for t in res["tips"])
    for tip in res["tips"]:
        assert "basis" in tip
        assert len(tip["basis"]) > 0

    # Briefing text contains events and citations [E...]
    briefing_text = res["briefing"]["text"]
    assert "Hampi Food Week" in briefing_text
    assert "[E" in briefing_text
    assert len(res["briefing"]["sources"]) > 0


def test_empty_week_reported_honestly_no_fabrication():
    """When moved to a date with 0 events (e.g. 2026-10-15 for Hampi),

    the briefing and happening section state plainly that no verified events
    are scheduled. No festival is fabricated.
    """
    city = _ensure_hampi()
    assert city is not None

    res = build_city_context(city, date(2026, 10, 15), briefing_mode="deferred")

    assert res["date"]["selected"] == "2026-10-15"

    # Events must be empty
    assert len(res["events"]["events"]) == 0
    assert "no verified events" in res["events"]["message"].lower()

    # Weather is from dataset daily record
    assert res["weather"]["status"] == "ok"
    assert res["weather"]["basis"] == "dataset"
    assert "dataset" in res["weather"]["source"].lower() or "dataset" in res["weather"]["basis_label"].lower()

    # Briefing text must plainly say no verified events and cite the source
    briefing_text = res["briefing"]["text"]
    assert "no verified events" in briefing_text.lower()

    # Check that no invented festival names appear in the briefing
    for fake_festival in ["Diwali Gala", "Hampi Light Carnival", "Global Heritage Expo"]:
        assert fake_festival.lower() not in briefing_text.lower()

    # Sources are cited with specific items
    assert len(res["briefing"]["sources"]) > 0
    assert any(s["source_type"] == "event_status" for s in res["briefing"]["sources"])
