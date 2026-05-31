"""Candidate lifecycle management — stavy, cooldown filter, history log."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from trendy.db import Candidate, CandidateStatusHistory, SuppressedCandidate
from trendy.config import settings

logger = logging.getLogger(__name__)

CandidateStatus = Literal[
    "new", "seen", "accepted", "in_progress", "rejected", "snoozed", "published"
]

REJECTION_REASONS = [
    "irelevantné pre portál",
    "nízka kvalita query",
    "duplicita existujúceho obsahu",
    "príliš nišové",
    "sezónna anomália",
    "iný dôvod",
]


def is_suppressed(candidate: Candidate) -> tuple[bool, str]:
    """
    Check if candidate should be suppressed (hidden) in current pipeline run.
    Returns (suppressed: bool, reason: str).
    """
    today = date.today()
    status = candidate.status

    if status in ("accepted", "in_progress"):
        return True, status

    if status == "published":
        days_since = (today - candidate.status_changed_at.date()).days
        cooldown = settings.cooldown_published_days
        if days_since < cooldown:
            return True, "cooldown_published"

    if status == "rejected":
        days_since = (today - candidate.status_changed_at.date()).days
        cooldown = settings.cooldown_rejected_days
        if days_since < cooldown:
            return True, "cooldown_rejected"

    if status == "snoozed":
        if candidate.snoozed_until and candidate.snoozed_until > today:
            return True, "snoozed"

    return False, ""


def apply_lifecycle_filter(
    candidates: list[Candidate],
    run_id: int,
    db: Session,
) -> tuple[list[Candidate], int]:
    """
    Filter candidates through lifecycle rules.
    Logs suppressed candidates to SuppressedCandidate table.
    Returns (active_candidates, suppressed_count).
    """
    active = []
    suppressed_count = 0

    for c in candidates:
        suppressed, reason = is_suppressed(c)
        if suppressed:
            db.add(SuppressedCandidate(
                run_id=run_id,
                candidate_id=c.id,
                suppressed_reason=reason,
            ))
            suppressed_count += 1
        else:
            active.append(c)

    db.flush()
    return active, suppressed_count


def change_status(
    candidate: Candidate,
    new_status: CandidateStatus,
    db: Session,
    reason: str | None = None,
    published_url: str | None = None,
    brief_url: str | None = None,
    snoozed_until: date | None = None,
) -> None:
    """
    Change candidate status and log to history.
    Handles all side-effects (set URLs, snoozed_until, etc.).
    """
    old_status = candidate.status

    # Status-specific side effects
    if new_status == "published" and published_url:
        candidate.published_url = published_url
    if new_status == "in_progress" and brief_url:
        candidate.brief_url = brief_url
    if new_status == "snoozed":
        candidate.snoozed_until = snoozed_until or _default_snooze_date()
    if new_status == "seen" and old_status in ("rejected", "snoozed", "published"):
        # Re-evaluation: clear old state
        candidate.snoozed_until = None
        candidate.is_returned_from_cooldown = True

    candidate.status = new_status
    candidate.status_changed_at = datetime.now(timezone.utc)

    db.add(CandidateStatusHistory(
        candidate_id=candidate.id,
        from_status=old_status,
        to_status=new_status,
        reason=reason,
    ))
    db.flush()
    logger.info("Candidate %d: %s → %s (reason: %s)", candidate.id, old_status, new_status, reason)


def bulk_change_status(
    candidate_ids: list[int],
    new_status: CandidateStatus,
    db: Session,
    reason: str | None = None,
    snoozed_until: date | None = None,
) -> int:
    """Bulk status change. Returns count of changed candidates."""
    count = 0
    for cid in candidate_ids:
        c = db.query(Candidate).get(cid)
        if c:
            change_status(c, new_status, db, reason=reason, snoozed_until=snoozed_until)
            count += 1
    db.commit()
    return count


def _default_snooze_date() -> date:
    from datetime import timedelta
    return date.today() + timedelta(days=settings.snooze_default_days)


def handle_returned_from_cooldown(candidate: Candidate, new_growth_score: float) -> bool:
    """
    When a previously rejected/snoozed/published candidate re-enters after cooldown,
    decide if it should be flagged as 'returned from cooldown' with re-evaluation prompt.
    Returns True if the flag was set.
    """
    if candidate.status in ("rejected", "snoozed") or (
        candidate.status == "published" and candidate.snoozed_until is None
    ):
        candidate.is_returned_from_cooldown = True
        return True
    return False


def get_suppressed_for_run(run_id: int, db: Session) -> list[dict]:
    """Return list of suppressed candidates for given pipeline run."""
    rows = (
        db.query(SuppressedCandidate, Candidate)
        .join(Candidate, SuppressedCandidate.candidate_id == Candidate.id)
        .filter(SuppressedCandidate.run_id == run_id)
        .all()
    )
    return [
        {
            "id": c.id,
            "keyword": c.keyword,
            "status": c.status,
            "suppressed_reason": s.suppressed_reason,
        }
        for s, c in rows
    ]
