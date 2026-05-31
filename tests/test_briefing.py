"""Tests for Phase 4 — briefing handoff."""
import json
import pytest
from trendy.db import Candidate, Portal
from trendy.briefing import (
    build_brief_data, export_candidate_brief,
    export_candidate_brief_json, PORTAL_BRIEF_SCHEMAS,
)


@pytest.fixture
def portal_and_candidate(db_session):
    portal = db_session.query(Portal).filter_by(key="msg-life").first()
    if not portal:
        portal = Portal(key="msg-life", name="msg-life.sk", url="https://www.msg-life.sk")
        db_session.add(portal)
        db_session.flush()

    c = Candidate(
        portal_id=portal.id,
        keyword="pracovný pohovor",
        keyword_normalized="pracovny pohovor",
        parent_topic="kariéra",
        cluster="Nábor",
        volume=1200,
        kd=35,
        intent="informational",
        source="ahrefs_keywords",
        trend_score=72.5,
        volume_score=65.0,
        growth_score=80.0,
        gap_score=100.0,
        opportunity_score=50.0,
        tag="rising",
        trend_mom_pct=45.0,
        trend_yoy_pct=30.0,
        gsc_avg_position=None,
        status="accepted",
    )
    db_session.add(c)
    db_session.commit()
    return portal, c


def test_build_brief_data_fields(db_session, portal_and_candidate):
    portal, c = portal_and_candidate
    data = build_brief_data(c.id, db=db_session)

    assert data["keyword"] == "pracovný pohovor"
    assert data["cluster"] == "Nábor"
    assert data["volume"] == 1200
    assert data["kd"] == 35
    assert data["tag"] == "rising"
    assert data["trend_score"] == 72.5
    assert data["portal_key"] == "msg-life"
    assert data["portal_name"] == "msg-life.sk"


def test_build_brief_data_has_notes(db_session, portal_and_candidate):
    _, c = portal_and_candidate
    data = build_brief_data(c.id, db=db_session)
    assert "brief_notes" in data
    assert len(data["brief_notes"]) > 0
    # Rising tag should mention rastúca
    assert "Rastúca" in data["brief_notes"] or "rising" in data["brief_notes"].lower() or "🚀" in data["brief_notes"]


def test_export_xlsx_returns_bytes(db_session, portal_and_candidate):
    _, c = portal_and_candidate
    result = export_candidate_brief(c.id, db=db_session)
    assert isinstance(result, bytes)
    assert len(result) > 100
    # XLSX magic bytes
    assert result[:4] == b"PK\x03\x04"


def test_export_json_valid(db_session, portal_and_candidate):
    _, c = portal_and_candidate
    json_str = export_candidate_brief_json(c.id, db=db_session)
    parsed = json.loads(json_str)
    assert parsed["keyword"] == "pracovný pohovor"
    assert parsed["portal_key"] == "msg-life"


def test_schemas_all_portals_defined():
    for portal_key in ["msg-life", "msgtester", "msgprogramator"]:
        assert portal_key in PORTAL_BRIEF_SCHEMAS
        schema = PORTAL_BRIEF_SCHEMAS[portal_key]
        assert "fields" in schema
        assert "keyword" in schema["fields"]
        assert "cluster" in schema["fields"]
        assert "trend_score" in schema["fields"]


def test_auto_brief_notes_rising(db_session, portal_and_candidate):
    from trendy.briefing import _auto_brief_notes
    _, c = portal_and_candidate
    notes = _auto_brief_notes(c)
    assert "🚀" in notes or "Rastúca" in notes


def test_auto_brief_notes_low_kd(db_session, portal_and_candidate):
    from trendy.briefing import _auto_brief_notes
    _, c = portal_and_candidate
    c.kd = 10
    notes = _auto_brief_notes(c)
    assert "nízka obtiažnosť" in notes or "KD=10" in notes
