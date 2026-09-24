"""Load the synthetic bootstrap feedback (development/demo) through the normal feedback pipeline.

    python -m app.feedback.bootstrap [--reload]
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.config import FEEDBACK_BOOTSTRAP_PATH
from app.db.models import Feedback, FeedbackAspect, FeedbackVibe, Poi
from app.db.session import SessionLocal
from app.feedback.aggregate import recompute_all
from app.feedback.service import FeedbackError, parse_input, submit_feedback

logger = logging.getLogger(__name__)


def remove_synthetic() -> int:
    with SessionLocal() as db:
        ids = [row[0] for row in db.execute(select(Feedback.id).where(Feedback.is_synthetic.is_(True))).all()]
        if ids:
            db.execute(delete(FeedbackVibe).where(FeedbackVibe.feedback_id.in_(ids)))
            db.execute(delete(FeedbackAspect).where(FeedbackAspect.feedback_id.in_(ids)))
            db.execute(delete(Feedback).where(Feedback.id.in_(ids)))
            db.commit()
    return len(ids)


def load_bootstrap(path=FEEDBACK_BOOTSTRAP_PATH) -> dict[str, int]:
    counts = {"loaded": 0, "skipped_unknown_place": 0, "invalid": 0}
    if not path.exists():
        return counts
    with SessionLocal() as db:
        known = {row[0] for row in db.execute(select(Poi.id)).all()}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["place_id"] not in known:
            counts["skipped_unknown_place"] += 1
            continue
        try:
            data = parse_input(record)
            created = datetime.combine(datetime.fromisoformat(record["visit_date"]).date(), datetime.min.time(), tzinfo=timezone.utc)
            submit_feedback(data, record["user_id"], synthetic=True, feedback_id=record["feedback_id"], created_at=created, recompute=False)
            counts["loaded"] += 1
        except FeedbackError as exc:
            counts["invalid"] += 1
            logger.warning("bootstrap_feedback_invalid id=%s error=%s", record.get("feedback_id"), exc.message)
    counts.update(recompute_all())
    logger.info("feedback_bootstrap %s", counts)
    return counts


def seed_feedback_if_needed() -> dict[str, int] | None:
    with SessionLocal() as db:
        existing = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.is_synthetic.is_(True)))
    if existing:
        return None
    return load_bootstrap()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load the synthetic bootstrap feedback.")
    parser.add_argument("--reload", action="store_true", help="remove existing synthetic feedback first")
    args = parser.parse_args()
    from app.db.session import init_db
    from app.feedback.vocabulary import sync_vocabulary

    init_db()
    sync_vocabulary()
    if args.reload:
        print("removed", remove_synthetic())
    print(load_bootstrap())
