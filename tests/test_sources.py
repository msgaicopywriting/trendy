"""Tests for Phase 1 — ingestion sources (offline, no real network)."""
import io
import csv
from datetime import date
from pathlib import Path
import pytest
import pandas as pd

from trendy.sources.base import CandidateRow
from trendy.sources.ahrefs import _parse_file, _infer_source_tag, _parse_volume, _parse_kd
from trendy.sources.gsc import _parse_date_from_filename, _parse_ctr, import_gsc_csv
from trendy.sources.clusters import _parse_cluster_file, assign_cluster
from trendy.sources.sitemap import normalize_slug
from trendy.db import Portal, GscQuery


# ────────── CandidateRow ──────────

def test_candidate_row_normalizes_keyword():
    c = CandidateRow(keyword="Pracovný Pohovor")
    assert c.keyword_normalized == "pracovny pohovor"


def test_candidate_row_preserves_explicit_normalized():
    c = CandidateRow(keyword="Test", keyword_normalized="custom normalized")
    assert c.keyword_normalized == "custom normalized"


# ────────── Ahrefs parser ──────────

def test_parse_volume_variants():
    assert _parse_volume("1,200") == 1200
    assert _parse_volume("2.5K") == 2500
    assert _parse_volume("1k") == 1000
    assert _parse_volume(None) == 0
    assert _parse_volume("") == 0


def test_parse_kd_clamp():
    assert _parse_kd("95") == 95
    assert _parse_kd(150) == 100
    assert _parse_kd(-5) == 0
    assert _parse_kd(None) is None


def test_infer_source_tag():
    assert _infer_source_tag("2024-01-15_competitor_keywords.csv") == "competitors"
    assert _infer_source_tag("2024-01-15_content_explorer.xlsx") == "content"
    assert _infer_source_tag("2024-01-15_brand_radar.csv") == "brand_radar"
    assert _infer_source_tag("2024-01-15_matching_terms.csv") == "keywords"


def test_parse_ahrefs_csv(tmp_path):
    csv_content = "keyword,volume,kd,intent,parent topic\npracovný pohovor,1200,35,informational,kariéra\nhomeoffice,800,28,informational,práca\n"
    f = tmp_path / "2024-01-15_keywords.csv"
    f.write_text(csv_content, encoding="utf-8")
    rows = _parse_file(f, "msg-life")
    assert len(rows) == 2
    assert rows[0].keyword == "pracovný pohovor"
    assert rows[0].volume == 1200
    assert rows[0].kd == 35
    assert rows[0].source == "ahrefs_keywords"
    assert rows[1].parent_topic == "práca"


def test_parse_ahrefs_csv_missing_keyword_col(tmp_path):
    csv_content = "query,volume\ntest,100\n"
    f = tmp_path / "2024-01-15_kw.csv"
    f.write_text(csv_content, encoding="utf-8")
    rows = _parse_file(f, "msg-life")
    assert len(rows) == 1
    assert rows[0].keyword == "test"


def test_parse_ahrefs_xlsx(tmp_path):
    df = pd.DataFrame({
        "Keyword": ["selenium testing", "cypress"],
        "Volume": [500, 300],
        "KD": [40, 55],
        "Intent": ["informational", "informational"],
    })
    f = tmp_path / "2024-01-15_kw.xlsx"
    df.to_excel(f, index=False)
    rows = _parse_file(f, "msgtester")
    assert len(rows) == 2


# ────────── GSC parser ──────────

def test_parse_date_from_filename():
    assert _parse_date_from_filename("2024-03-15_queries.csv") == date(2024, 3, 15)
    assert _parse_date_from_filename("queries.csv") == date.today()


def test_parse_ctr():
    assert abs(_parse_ctr("3.5%") - 0.035) < 1e-6
    assert abs(_parse_ctr(0.05) - 0.05) < 1e-6
    assert _parse_ctr(None) == 0.0


def test_import_gsc_csv(tmp_path, db_session):
    portal = Portal(key="msgtester", name="GSC Test", url="https://test.sk")
    db_session.add(portal)
    db_session.flush()

    csv_content = "Top queries,Clicks,Impressions,CTR,Average position\ntestovanie softveru,50,500,10%,8.5\nQA automatizacia,20,200,10%,12.3\n"
    portal_dir = tmp_path / "msgtester"
    portal_dir.mkdir()
    f = portal_dir / "2024-03-15_queries.csv"
    f.write_text(csv_content, encoding="utf-8")

    count = import_gsc_csv(portal, db_session, inbox_dir=tmp_path)
    assert count == 2

    rows = db_session.query(GscQuery).filter_by(portal_id=portal.id).all()
    assert len(rows) == 2
    queries = {r.query for r in rows}
    assert "testovanie softveru" in queries
    assert rows[0].export_date == date(2024, 3, 15)


def test_import_gsc_csv_idempotent(tmp_path, db_session):
    """Second import of same file should not duplicate rows."""
    portal = Portal(key="msg-life", name="GSC Idem", url="https://idem.sk")
    db_session.add(portal)
    db_session.flush()

    csv_content = "Top queries,Clicks,Impressions,CTR,Average position\ntest query,10,100,10%,5.0\n"
    portal_dir = tmp_path / "msg-life"
    portal_dir.mkdir()
    f = portal_dir / "2024-03-15_queries.csv"
    f.write_text(csv_content, encoding="utf-8")

    import_gsc_csv(portal, db_session, inbox_dir=tmp_path)
    import_gsc_csv(portal, db_session, inbox_dir=tmp_path)

    count = db_session.query(GscQuery).filter_by(portal_id=portal.id).count()
    assert count == 1


# ────────── Clusters ──────────

def test_parse_cluster_file(tmp_path):
    df = pd.DataFrame({
        "keyword": ["pracovný pohovor", "životopis", "homeoffice"],
        "cluster": ["Nábor", "Nábor", "Flexibilná práca"],
    })
    f = tmp_path / "clusters.xlsx"
    df.to_excel(f, index=False)

    mapping = _parse_cluster_file(f)
    assert mapping.get("pracovny pohovor") == "Nábor"
    assert mapping.get("zivotopis") == "Nábor"
    assert mapping.get("homeoffice") == "Flexibilná práca"


def test_assign_cluster_exact(tmp_path, monkeypatch):
    from trendy.sources import clusters as clusters_mod
    clusters_mod.load_clusters.cache_clear()

    cluster_dir = tmp_path / "msg-life"
    cluster_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "keyword": ["python tutorial", "javascript"],
        "cluster": ["Python", "JS"],
    })
    df.to_excel(cluster_dir / "master.xlsx", index=False)

    monkeypatch.setattr("trendy.sources.clusters.settings.clusters_dir", tmp_path)
    clusters_mod.load_clusters.cache_clear()

    result = assign_cluster("python tutorial", "msg-life")
    assert result == "Python"


# ────────── Sitemap utils ──────────

def test_normalize_slug():
    assert normalize_slug("Pracovný Pohovor Tips") == "pracovny pohovor tips"
    assert normalize_slug(None) == ""
    assert normalize_slug("") == ""
