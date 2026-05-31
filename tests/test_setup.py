"""Smoke tests for Phase 0 — config, DB schema, portal registry."""
from trendy.config import settings, PORTALS
from trendy.db import Portal, Candidate, PublishedArticle, GscQuery, CandidateStatusHistory, PipelineRun


def test_portals_defined():
    assert set(PORTALS.keys()) == {"msg-life", "msgtester", "msgprogramator"}


def test_portal_volume_thresholds():
    assert PORTALS["msg-life"].volume_threshold == settings.threshold_msg_life
    assert PORTALS["msgtester"].volume_threshold == settings.threshold_msgtester
    assert PORTALS["msgprogramator"].volume_threshold == settings.threshold_msgprogramator


def test_scoring_weights_sum_to_one():
    total = (
        settings.weight_volume
        + settings.weight_growth
        + settings.weight_gap
        + settings.weight_opportunity
    )
    assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"


def test_db_tables_created(test_engine):
    from sqlalchemy import inspect
    inspector = inspect(test_engine)
    tables = set(inspector.get_table_names())
    expected = {
        "portals", "published_articles", "gsc_queries", "candidates",
        "candidate_status_history", "pipeline_runs", "suppressed_candidates",
    }
    assert expected.issubset(tables)


def test_portal_insert_and_query(db_session):
    p = Portal(key="test-portal", name="Test Portal", url="https://test.sk")
    db_session.add(p)
    db_session.commit()
    fetched = db_session.query(Portal).filter_by(key="test-portal").first()
    assert fetched is not None
    assert fetched.url == "https://test.sk"


def test_candidate_default_status(db_session):
    portal = Portal(key="p-status-test", name="P", url="https://p.sk")
    db_session.add(portal)
    db_session.flush()
    c = Candidate(
        portal_id=portal.id,
        keyword="testovanie",
        keyword_normalized="testovanie",
    )
    db_session.add(c)
    db_session.commit()
    assert c.status == "new"
