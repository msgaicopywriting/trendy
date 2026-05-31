"""Tests for the needs_volume flag + Ahrefs volume pairing in the pipeline."""
import pytest

from trendy.sources.base import CandidateRow
from trendy.db import Portal, Candidate
from trendy.pipeline import run_pipeline


@pytest.fixture
def mock_sources(monkeypatch):
    """Mock every pipeline source to an offline no-op.

    Returns a mutable dict whose 'ahrefs' and 'reddit' lists tests populate
    with CandidateRows to drive a run.
    """
    feed = {"ahrefs": [], "reddit": []}

    monkeypatch.setattr("trendy.pipeline.sitemap.refresh_sitemap", lambda *a, **k: None)
    monkeypatch.setattr("trendy.pipeline.sitemap.get_covered_slugs", lambda *a, **k: set())
    monkeypatch.setattr("trendy.pipeline.gsc.import_gsc_csv", lambda *a, **k: 0)
    monkeypatch.setattr("trendy.pipeline.gsc.get_covered_queries", lambda *a, **k: {})
    monkeypatch.setattr("trendy.pipeline.gsc.get_rising_candidates_via_llm", lambda *a, **k: [])
    monkeypatch.setattr("trendy.pipeline.ahrefs.load_inbox", lambda *a, **k: list(feed["ahrefs"]))
    monkeypatch.setattr("trendy.pipeline.reddit_fetch", lambda *a, **k: list(feed["reddit"]))
    monkeypatch.setattr("trendy.pipeline.fetch_rss_candidates", lambda *a, **k: [])
    monkeypatch.setattr("trendy.pipeline.fetch_claude_probe", lambda *a, **k: [])
    monkeypatch.setattr("trendy.pipeline.fetch_perplexity_probe", lambda *a, **k: [])
    monkeypatch.setattr("trendy.pipeline.trends.fetch_trend_data", lambda *a, **k: {})
    monkeypatch.setattr("trendy.pipeline.trends.fetch_rising_queries", lambda *a, **k: [])
    monkeypatch.setattr("trendy.pipeline.clusters.assign_cluster", lambda *a, **k: None)
    return feed


@pytest.fixture
def portal_ml(db_session):
    p = Portal(key="msg-life", name="msg-life.sk", url="https://www.msg-life.sk")
    db_session.add(p)
    db_session.flush()
    return p


def _get(db_session, portal, kw_norm):
    return (
        db_session.query(Candidate)
        .filter_by(portal_id=portal.id, keyword_normalized=kw_norm)
        .first()
    )


def test_discovery_candidate_flagged_needs_volume(db_session, portal_ml, mock_sources):
    mock_sources["reddit"] = [CandidateRow(keyword="ako uspiet na pohovore", source="reddit")]
    run_pipeline("msg-life", db=db_session)

    c = _get(db_session, portal_ml, "ako uspiet na pohovore")
    assert c is not None
    assert c.needs_volume
    assert c.volume == 0


def test_ahrefs_candidate_not_flagged(db_session, portal_ml, mock_sources):
    mock_sources["ahrefs"] = [
        CandidateRow(keyword="pracovny pohovor", volume=1500, kd=30, source="ahrefs_keywords")
    ]
    run_pipeline("msg-life", db=db_session)

    c = _get(db_session, portal_ml, "pracovny pohovor")
    assert c is not None
    assert not c.needs_volume
    assert c.volume == 1500


def test_ahrefs_volume_closes_loop(db_session, portal_ml, mock_sources):
    # 1st run: discovery topic with no volume → flagged needs_volume
    mock_sources["reddit"] = [CandidateRow(keyword="pracovny pohovor", source="reddit")]
    run_pipeline("msg-life", db=db_session)
    c = _get(db_session, portal_ml, "pracovny pohovor")
    assert c.needs_volume
    score_before = c.trend_score

    # 2nd run: Ahrefs provides real volume for the same normalized keyword
    mock_sources["reddit"] = []
    mock_sources["ahrefs"] = [
        CandidateRow(keyword="pracovný pohovor", volume=1500, kd=25, source="ahrefs_keywords")
    ]
    run_pipeline("msg-life", db=db_session)

    c = _get(db_session, portal_ml, "pracovny pohovor")
    assert not c.needs_volume
    assert c.volume == 1500
    assert c.volume_score > 0
    # real volume lifts the composite score vs. the volume-less version
    assert c.trend_score >= score_before


def test_discovery_volume_does_not_erase_real_volume(db_session, portal_ml, mock_sources):
    # Ahrefs first establishes a real volume
    mock_sources["ahrefs"] = [
        CandidateRow(keyword="pracovný pohovor", volume=1500, kd=25, source="ahrefs_keywords")
    ]
    run_pipeline("msg-life", db=db_session)

    # A later volume-less discovery re-fetch of the same topic must not zero it
    mock_sources["ahrefs"] = []
    mock_sources["reddit"] = [CandidateRow(keyword="pracovny pohovor", source="reddit")]
    run_pipeline("msg-life", db=db_session)

    c = _get(db_session, portal_ml, "pracovny pohovor")
    assert c.volume == 1500
    assert not c.needs_volume
