"""Tests for data-derived + manual seed keyword management."""
import json
from datetime import date

import pytest

from trendy.db import Portal, Seed, GscQuery, Candidate
from trendy import seeds as seeds_mod
from trendy.seeds import (
    get_active_seeds, refresh_auto_seeds, add_manual_seed,
    remove_seed, set_seed_active, select_seeds_for_run,
)


@pytest.fixture
def portal_seed(db_session):
    p = Portal(key="msg-life", name="msg-life.sk", url="https://www.msg-life.sk")
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture(autouse=True)
def _no_real_sitemap_fetch(monkeypatch):
    """refresh_auto_seeds opportunistically fetches the sitemap over the network
    when a portal has no PublishedArticle rows yet — every test here starts with
    an empty DB, so without this a plain unit test call turns into a real HTTP
    crawl of msg-life.sk (previously ballooned a run of this file to ~98 minutes).
    Tests that want to exercise the real fetch path override this again locally."""
    import trendy.sources.sitemap as sitemap_mod
    monkeypatch.setattr(sitemap_mod, "refresh_sitemap", lambda *a, **k: 0)


def test_get_active_seeds_bootstraps_from_config(db_session, portal_seed):
    result = get_active_seeds(portal_seed, db_session)
    assert len(result) > 0
    stored = db_session.query(Seed).filter_by(portal_id=portal_seed.id).all()
    assert all(s.origin == "bootstrap" for s in stored)


def test_refresh_skips_without_llm(db_session, portal_seed, monkeypatch):
    monkeypatch.setattr(seeds_mod, "llm_available", lambda: False)
    result = refresh_auto_seeds(portal_seed, db_session)
    assert result["status"] == "skipped"


def test_refresh_skips_without_evidence(db_session, portal_seed, monkeypatch):
    monkeypatch.setattr(seeds_mod, "llm_available", lambda: True)
    result = refresh_auto_seeds(portal_seed, db_session)
    assert result["status"] == "skipped"
    assert "evidence" in result["reason"]


def test_refresh_replaces_auto_but_keeps_manual(db_session, portal_seed, monkeypatch):
    add_manual_seed(portal_seed, "moje vlastné seedo", db_session)

    db_session.add(GscQuery(
        portal_id=portal_seed.id, query="pracovny pohovor", impressions=500,
        export_date=date(2026, 6, 1),
    ))
    db_session.commit()

    monkeypatch.setattr(seeds_mod, "llm_available", lambda: True)
    monkeypatch.setattr(seeds_mod, "llm_complete", lambda *a, **k: json.dumps(
        [{"keyword": "pracovny pohovor", "evidence": "GSC top query"}]
    ))

    result = refresh_auto_seeds(portal_seed, db_session)
    assert result["status"] == "ok"
    assert result["added"] == 1

    all_seeds = db_session.query(Seed).filter_by(portal_id=portal_seed.id).all()
    origins = {s.keyword_normalized: s.origin for s in all_seeds}
    assert origins["moje vlastne seedo"] == "manual"
    assert origins["pracovny pohovor"] == "auto"


def test_refresh_dedupes_against_manual(db_session, portal_seed, monkeypatch):
    add_manual_seed(portal_seed, "python tutorial", db_session)

    db_session.add(GscQuery(
        portal_id=portal_seed.id, query="python tutorial", impressions=100,
        export_date=date(2026, 6, 1),
    ))
    db_session.commit()

    monkeypatch.setattr(seeds_mod, "llm_available", lambda: True)
    monkeypatch.setattr(seeds_mod, "llm_complete", lambda *a, **k: json.dumps(
        [{"keyword": "python tutorial", "evidence": "GSC"}]
    ))

    refresh_auto_seeds(portal_seed, db_session)

    matches = db_session.query(Seed).filter_by(
        portal_id=portal_seed.id, keyword_normalized="python tutorial"
    ).all()
    assert len(matches) == 1
    assert matches[0].origin == "manual"


def test_refresh_recovers_session_after_sitemap_failure(db_session, portal_seed, monkeypatch):
    """A failed opportunistic sitemap fetch (e.g. a DB integrity error from a
    concurrent write) must not leave the session in a broken PendingRollbackError
    state for the rest of refresh_auto_seeds — this crashed a real run when the
    caught exception wasn't followed by a rollback."""
    def _boom(portal, db, fetch_meta=True):
        dup = dict(portal_id=portal.id, keyword="dup", keyword_normalized="dup", origin="auto", active=True)
        db.add(Seed(**dup))
        db.add(Seed(**dup))
        db.commit()  # raises IntegrityError (unique constraint), leaving session dirty

    import trendy.sources.sitemap as sitemap_mod
    monkeypatch.setattr(sitemap_mod, "refresh_sitemap", _boom)
    monkeypatch.setattr(seeds_mod, "llm_available", lambda: True)

    db_session.add(GscQuery(
        portal_id=portal_seed.id, query="test query", impressions=10,
        export_date=date(2026, 6, 1),
    ))
    db_session.commit()

    monkeypatch.setattr(seeds_mod, "llm_complete", lambda *a, **k: json.dumps(
        [{"keyword": "test query", "evidence": "GSC"}]
    ))

    result = refresh_auto_seeds(portal_seed, db_session)
    assert result["status"] == "ok"


def test_collect_evidence_competitor_gap(db_session, portal_seed):
    """Competitor keywords we don't cover (source=ahrefs_competitors, no matched
    article) land in the competitor_gaps bucket; our own covered ones don't."""
    db_session.add(Candidate(
        portal_id=portal_seed.id, keyword="salary calculator", keyword_normalized="salary calculator",
        parent_topic="mzdová kalkulačka", source="ahrefs_competitors", volume=800,
        matched_article_id=None,
    ))
    db_session.add(Candidate(
        portal_id=portal_seed.id, keyword="our own topic", keyword_normalized="our own topic",
        parent_topic="náš vlastný", source="ahrefs_keywords", volume=500,
    ))
    db_session.commit()

    evidence = seeds_mod._collect_evidence(portal_seed, db_session)
    assert "mzdová kalkulačka" in evidence["competitor_gaps"]
    assert "náš vlastný" in evidence["own_topics"]
    assert "mzdová kalkulačka" not in evidence["own_topics"]


def test_refresh_folds_expansion_type_into_evidence(db_session, portal_seed, monkeypatch):
    db_session.add(GscQuery(
        portal_id=portal_seed.id, query="anchor", impressions=10, export_date=date(2026, 6, 1),
    ))
    db_session.commit()

    monkeypatch.setattr(seeds_mod, "llm_available", lambda: True)
    monkeypatch.setattr(seeds_mod, "llm_complete", lambda *a, **k: json.dumps([
        {"keyword": "core topic", "type": "coverage", "evidence": "GSC dopyt"},
        {"keyword": "adjacent topic", "type": "expansion", "evidence": "konkurenčná medzera"},
    ]))

    result = refresh_auto_seeds(portal_seed, db_session)
    assert result["status"] == "ok"

    seeds = {s.keyword_normalized: s for s in db_session.query(Seed).filter_by(portal_id=portal_seed.id)}
    assert "expansion" in seeds["adjacent topic"].source_evidence
    assert "expansion" not in seeds["core topic"].source_evidence


def test_add_manual_seed_promotes_existing_auto(db_session, portal_seed):
    db_session.add(Seed(
        portal_id=portal_seed.id, keyword="devops", keyword_normalized="devops",
        origin="auto", active=True,
    ))
    db_session.commit()

    add_manual_seed(portal_seed, "devops", db_session)

    seed = db_session.query(Seed).filter_by(portal_id=portal_seed.id, keyword_normalized="devops").first()
    assert seed.origin == "manual"


def test_remove_and_deactivate_seed(db_session, portal_seed):
    seed = add_manual_seed(portal_seed, "test seed", db_session)

    set_seed_active(seed.id, False, db_session)
    db_session.refresh(seed)
    assert not seed.active

    remove_seed(seed.id, db_session)
    assert db_session.query(Seed).filter_by(id=seed.id).first() is None


def test_select_seeds_for_run_rotates():
    all_seeds = [f"seed{i}" for i in range(12)]
    window0 = select_seeds_for_run(all_seeds, run_index=0, batch=5)
    window1 = select_seeds_for_run(all_seeds, run_index=1, batch=5)
    assert len(window0) == 5
    assert window0 != window1


def test_select_seeds_for_run_returns_all_when_under_batch():
    all_seeds = ["a", "b", "c"]
    assert select_seeds_for_run(all_seeds, run_index=5, batch=5) == all_seeds
