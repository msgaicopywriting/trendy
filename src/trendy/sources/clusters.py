"""Cluster master file loader — číta XLSX per portál, mapu klastrov."""
from __future__ import annotations

import logging
from pathlib import Path
from functools import lru_cache

import pandas as pd
from slugify import slugify

from trendy.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def load_clusters(portal_key: str, clusters_dir: Path | None = None) -> dict[str, str]:
    """
    Load cluster master XLSX for portal_key.
    Returns dict: {keyword_normalized → cluster_name}.

    Looks for any .xlsx file in data/clusters/<portal_key>/.
    Expects at minimum columns: 'keyword' (or 'kw') and 'cluster' (or 'topic').
    Different portals may have different schemas — the loader tries common column names.
    """
    base = clusters_dir or settings.clusters_dir
    portal_dir = Path(base) / portal_key

    if not portal_dir.exists():
        logger.info("No clusters dir for %s", portal_key)
        return {}

    xlsx_files = list(portal_dir.glob("*.xlsx"))
    if not xlsx_files:
        logger.info("No cluster XLSX for %s", portal_key)
        return {}

    # Use most recently modified file
    fpath = max(xlsx_files, key=lambda p: p.stat().st_mtime)
    logger.info("Loading cluster master: %s", fpath.name)

    try:
        return _parse_cluster_file(fpath)
    except Exception as e:
        logger.error("Failed to load cluster file %s: %s", fpath, e)
        return {}


def _parse_cluster_file(fpath: Path) -> dict[str, str]:
    """Try each sheet and common column name patterns to extract keyword→cluster mapping."""
    xl = pd.ExcelFile(fpath, engine="openpyxl")
    mapping: dict[str, str] = {}

    keyword_cols = {"keyword", "kw", "query", "klucove slovo", "kľúčové slovo", "topic", "téma"}
    cluster_cols = {"cluster", "klaster", "pillar", "pilier", "group", "skupna", "skupina", "category"}

    for sheet in xl.sheet_names:
        try:
            df = xl.parse(sheet)
            if df.empty:
                continue

            df.columns = [str(c).strip().lower() for c in df.columns]

            kw_col = next((c for c in df.columns if c in keyword_cols), None)
            cl_col = next((c for c in df.columns if c in cluster_cols), None)

            if not kw_col or not cl_col:
                continue

            for _, row in df.iterrows():
                kw = str(row.get(kw_col, "")).strip()
                cl = str(row.get(cl_col, "")).strip()
                if kw and cl and kw.lower() not in ("nan", "none", ""):
                    norm = slugify(kw, separator=" ", lowercase=True)
                    mapping[norm] = cl

        except Exception as e:
            logger.warning("Error parsing sheet '%s': %s", sheet, e)
            continue

    logger.info("Loaded %d cluster entries from %s", len(mapping), fpath.name)
    return mapping


def assign_cluster(keyword_normalized: str, portal_key: str) -> str | None:
    """
    Return cluster name for a normalized keyword.
    Uses exact match first, then falls back to substring match.
    """
    mapping = load_clusters(portal_key)
    if not mapping:
        return None

    # Exact match
    if keyword_normalized in mapping:
        return mapping[keyword_normalized]

    # Substring: keyword contains a known cluster keyword
    kw_words = set(keyword_normalized.split())
    best: str | None = None
    best_len = 0
    for mapped_kw, cluster in mapping.items():
        mapped_words = set(mapped_kw.split())
        overlap = kw_words & mapped_words
        if overlap and len(overlap) > best_len:
            best_len = len(overlap)
            best = cluster

    return best


def invalidate_cache(portal_key: str | None = None):
    """Call after updating cluster master file."""
    load_clusters.cache_clear()
