"""Tests for cross-source candidate merging (Fáza 1 optimization)."""
from trendy.pipeline import merge_candidates
from trendy.sources.base import CandidateRow


def test_merge_dedupes_by_keyword_normalized():
    rows = [
        CandidateRow(keyword="homeoffice", source="llm_probe"),
        CandidateRow(keyword="Homeoffice", source="rss_llm"),
    ]
    merged = merge_candidates(rows)
    assert len(merged) == 1
    assert merged[0].extra["sources"] == ["llm_probe", "rss_llm"]


def test_merge_prefers_ahrefs_as_primary():
    rows = [
        CandidateRow(keyword="python tutorial", source="llm_probe"),
        CandidateRow(keyword="python tutorial", source="ahrefs_keywords", volume=800, kd=35),
    ]
    merged = merge_candidates(rows)
    assert len(merged) == 1
    assert merged[0].source == "ahrefs_keywords"
    assert merged[0].kd == 35
    assert merged[0].volume == 800


def test_merge_volume_takes_max():
    rows = [
        CandidateRow(keyword="cypress", source="reddit", volume=0),
        CandidateRow(keyword="cypress", source="ahrefs_keywords", volume=200),
    ]
    merged = merge_candidates(rows)
    assert merged[0].volume == 200


def test_merge_no_duplicates_stays_unchanged():
    rows = [
        CandidateRow(keyword="onboarding", source="llm_probe"),
        CandidateRow(keyword="offboarding", source="llm_probe"),
    ]
    merged = merge_candidates(rows)
    assert len(merged) == 2


def test_merge_source_count_reflected_in_sources_list():
    rows = [
        CandidateRow(keyword="devops", source="reddit"),
        CandidateRow(keyword="devops", source="rss_llm"),
        CandidateRow(keyword="devops", source="llm_probe"),
    ]
    merged = merge_candidates(rows)
    assert len(merged[0].extra["sources"]) == 3
