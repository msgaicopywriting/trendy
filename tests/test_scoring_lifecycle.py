"""Tests for Phase 2 — scoring + lifecycle."""
import pytest
from datetime import date, datetime, timedelta, timezone

from trendy.scoring import compute_score, ScoringInput, _volume_score, _growth_score, _gap_score, _saturate
from trendy.lifecycle import is_suppressed, change_status, REJECTION_REASONS
from trendy.db import Candidate, Portal, CandidateStatusHistory, PipelineRun


# ────────── Scoring ──────────

def test_volume_score_zero():
    assert _volume_score(0) == 0.0


def test_volume_score_reference():
    # 10K volume should give ~100
    assert _volume_score(10_000) >= 95.0


def test_volume_score_log_scale():
    low = _volume_score(100)
    mid = _volume_score(1000)
    high = _volume_score(5000)
    assert low < mid < high


def test_growth_score_positive():
    score = _growth_score(80.0, 50.0)
    assert score > 40


def test_growth_score_negative_clamps_to_zero():
    assert _growth_score(-50.0, -30.0) == 0.0


def test_saturate_monotonic_no_hard_cap():
    # No cliff at 100%: growth keeps climbing (but saturates) well past the old cap
    assert _saturate(100) < _saturate(500) < _saturate(5000) < 100.0


def test_source_count_bonus_increases_score():
    base = compute_score(ScoringInput(volume=500, trend_mom_pct=0.0, source_count=1))
    boosted = compute_score(ScoringInput(volume=500, trend_mom_pct=0.0, source_count=3))
    assert boosted.trend_score > base.trend_score


def test_gap_score_full_gap():
    assert _gap_score(None, False) == 100.0


def test_gap_score_top_10():
    assert _gap_score(5.0, True) == 0.0


def test_gap_score_11_to_20():
    assert _gap_score(15.0, True) == 50.0


def test_compute_score_rising_tag():
    result = compute_score(ScoringInput(
        volume=1000,
        kd=20,
        intent="informational",
        trend_mom_pct=80.0,
        trend_yoy_pct=60.0,
        gsc_avg_position=None,
        has_published_article=False,
        source="ahrefs_keywords",
    ))
    assert result.tag == "rising"
    assert result.trend_score > 50


def test_compute_score_gap_tag():
    result = compute_score(ScoringInput(
        volume=500,
        kd=30,
        intent="informational",
        trend_mom_pct=0.0,
        trend_yoy_pct=0.0,
        gsc_avg_position=None,
        has_published_article=False,
        source="ahrefs_keywords",
    ))
    assert result.tag == "gap"


def test_compute_score_newly_discovered():
    result = compute_score(ScoringInput(
        volume=0,
        kd=None,
        trend_mom_pct=30.0,
        days_since_first_seen=0,
        source="pytrends_rising",
    ))
    assert result.tag == "newly_discovered"


def test_weights_sum():
    from trendy.config import settings
    total = (
        settings.weight_volume + settings.weight_growth
        + settings.weight_gap + settings.weight_opportunity
    )
    assert abs(total - 1.0) < 1e-6


# ────────── Lifecycle ──────────

def _make_candidate(db_session, portal, status="new", **kwargs):
    c = Candidate(
        portal_id=portal.id,
        keyword=kwargs.get("keyword", "test keyword"),
        keyword_normalized=kwargs.get("kw_norm", "test keyword"),
        status=status,
        **{k: v for k, v in kwargs.items() if k not in ("keyword", "kw_norm")},
    )
    db_session.add(c)
    db_session.flush()
    return c


@pytest.fixture
def portal_lc(db_session):
    p = Portal(key="lc-portal", name="LC", url="https://lc.sk")
    db_session.add(p)
    db_session.flush()
    return p


def test_new_not_suppressed(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="new")
    suppressed, reason = is_suppressed(c)
    assert not suppressed


def test_accepted_suppressed(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="accepted")
    suppressed, reason = is_suppressed(c)
    assert suppressed
    assert reason == "accepted"


def test_in_progress_suppressed(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="in_progress")
    suppressed, _ = is_suppressed(c)
    assert suppressed


def test_rejected_in_cooldown_suppressed(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="rejected")
    # status_changed_at defaults to now → cooldown not expired
    suppressed, reason = is_suppressed(c)
    assert suppressed
    assert reason == "cooldown_rejected"


def test_rejected_after_cooldown_not_suppressed(db_session, portal_lc):
    from trendy.config import settings
    c = _make_candidate(db_session, portal_lc, status="rejected")
    # Simulate cooldown expired
    expired = datetime.now(timezone.utc) - timedelta(days=settings.cooldown_rejected_days + 1)
    c.status_changed_at = expired
    db_session.flush()
    suppressed, _ = is_suppressed(c)
    assert not suppressed


def test_snoozed_active_suppressed(db_session, portal_lc):
    future = date.today() + timedelta(days=10)
    c = _make_candidate(db_session, portal_lc, status="snoozed", snoozed_until=future)
    suppressed, reason = is_suppressed(c)
    assert suppressed
    assert reason == "snoozed"


def test_snoozed_expired_not_suppressed(db_session, portal_lc):
    past = date.today() - timedelta(days=1)
    c = _make_candidate(db_session, portal_lc, status="snoozed", snoozed_until=past)
    suppressed, _ = is_suppressed(c)
    assert not suppressed


def test_change_status_logs_history(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="new")
    change_status(c, "accepted", db_session, reason="dobrá téma")
    assert c.status == "accepted"

    history = db_session.query(CandidateStatusHistory).filter_by(candidate_id=c.id).all()
    assert len(history) == 1
    assert history[0].from_status == "new"
    assert history[0].to_status == "accepted"
    assert history[0].reason == "dobrá téma"


def test_change_status_rejected_with_reason(db_session, portal_lc):
    c = _make_candidate(db_session, portal_lc, status="seen")
    change_status(c, "rejected", db_session, reason="irelevantné pre portál")
    assert c.status == "rejected"
    history = db_session.query(CandidateStatusHistory).filter_by(candidate_id=c.id).all()
    assert history[0].reason == "irelevantné pre portál"


def test_rejection_reasons_list():
    assert len(REJECTION_REASONS) >= 4
    assert "irelevantné pre portál" in REJECTION_REASONS


def test_published_cooldown_suppressed(db_session, portal_lc):
    from trendy.config import settings
    c = _make_candidate(db_session, portal_lc, status="published")
    # Just published — should be suppressed
    suppressed, reason = is_suppressed(c)
    assert suppressed
    assert reason == "cooldown_published"
