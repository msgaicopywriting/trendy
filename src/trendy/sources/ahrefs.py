"""Ahrefs source — CSV/XLSX import now, MCP stub for future automation."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from datetime import date

import pandas as pd
from slugify import slugify

from trendy.sources.base import CandidateRow
from trendy.config import settings

logger = logging.getLogger(__name__)

# Column name aliases: maps common Ahrefs export column variants → canonical name
_COL_MAP = {
    # Keywords Explorer: Matching terms / Related terms
    "keyword": "keyword",
    "kw": "keyword",
    "query": "keyword",
    # Volume
    "volume": "volume",
    "search volume": "volume",
    "monthly volume": "volume",
    "avg. monthly searches": "volume",
    # KD
    "kd": "kd",
    "keyword difficulty": "kd",
    "difficulty": "kd",
    # Parent topic
    "parent topic": "parent_topic",
    "topic": "parent_topic",
    # Intent
    "intent": "intent",
    "search intent": "intent",
    # CPC (not used for scoring but kept for reference)
    "cpc": "cpc",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase + strip column names, then map to canonical names."""
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {c: _COL_MAP[c] for c in df.columns if c in _COL_MAP}
    return df.rename(columns=rename)


def _parse_volume(val) -> int:
    if pd.isna(val):
        return 0
    s = str(val).replace(",", "").replace(" ", "").replace("\xa0", "")
    # Handle "1K", "2.5K" notation
    m = re.match(r"([\d.]+)[kK]", s)
    if m:
        return int(float(m.group(1)) * 1000)
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _parse_kd(val) -> int | None:
    if pd.isna(val):
        return None
    try:
        return max(0, min(100, int(float(str(val)))))
    except (ValueError, TypeError):
        return None


def load_inbox(portal_key: str, inbox_dir: Path | None = None) -> list[CandidateRow]:
    """
    Load all unprocessed Ahrefs CSV/XLSX files from
    data/ahrefs_inbox/<portal_key>/ and return CandidateRow list.

    Files are considered "processed" simply by being read — no deletion,
    just date-stamped filenames ensure idempotent re-reads (dedup happens
    at DB upsert level via keyword_normalized unique constraint).
    """
    base = inbox_dir or settings.ahrefs_inbox_dir
    portal_dir = Path(base) / portal_key
    if not portal_dir.exists():
        logger.warning("Ahrefs inbox not found: %s", portal_dir)
        return []

    rows: list[CandidateRow] = []
    files = sorted(portal_dir.glob("*.csv")) + sorted(portal_dir.glob("*.xlsx"))

    if not files:
        logger.info("No Ahrefs files in %s", portal_dir)
        return []

    for fpath in files:
        try:
            rows.extend(_parse_file(fpath, portal_key))
        except Exception as e:
            logger.error("Failed to parse %s: %s", fpath.name, e)

    logger.info("Loaded %d candidates from %d Ahrefs files for %s", len(rows), len(files), portal_key)
    return rows


def _parse_file(fpath: Path, portal_key: str) -> list[CandidateRow]:
    source_tag = _infer_source_tag(fpath.name)

    if fpath.suffix.lower() == ".xlsx":
        df = pd.read_excel(fpath, engine="openpyxl")
    else:
        # Try comma first, then semicolon (Ahrefs sometimes uses semicolons)
        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(fpath, sep=";", encoding="utf-8-sig")

    df = _normalize_columns(df)

    if "keyword" not in df.columns:
        logger.warning("No 'keyword' column in %s, skipping. Columns: %s", fpath.name, list(df.columns))
        return []

    rows = []
    for _, row in df.iterrows():
        kw = str(row.get("keyword", "")).strip()
        if not kw or kw.lower() in ("keyword", "query", "kw", ""):
            continue

        rows.append(CandidateRow(
            keyword=kw,
            keyword_normalized=slugify(kw, separator=" ", lowercase=True),
            parent_topic=str(row["parent_topic"]).strip() if "parent_topic" in row and not pd.isna(row.get("parent_topic")) else None,
            volume=_parse_volume(row.get("volume", 0)),
            kd=_parse_kd(row.get("kd")),
            intent=str(row["intent"]).strip().lower() if "intent" in row and not pd.isna(row.get("intent")) else None,
            source=f"ahrefs_{source_tag}",
            extra={"file": fpath.name},
        ))

    return rows


def _infer_source_tag(filename: str) -> str:
    """Infer source type from filename convention: YYYY-MM-DD_<type>.csv"""
    fn = filename.lower()
    if "competitor" in fn or "site_explorer" in fn:
        return "competitors"
    if "content" in fn or "newly" in fn:
        return "content"
    if "brand" in fn or "radar" in fn:
        return "brand_radar"
    return "keywords"


# ---------------------------------------------------------------------------
# MCP stub — will replace load_inbox() when Ahrefs MCP is connected
# ---------------------------------------------------------------------------

class AhrefsMCPStub:
    """
    Placeholder for future Ahrefs MCP integration.

    When the MCP connector is active, swap `load_inbox()` calls in
    pipeline.py for `AhrefsMCPStub().fetch(portal_key)`.
    The output interface (list[CandidateRow]) stays identical.
    """
    name = "ahrefs_mcp"
    _connected = False  # flip to True when MCP tools are verified available

    def fetch(self, portal_key: str) -> list[CandidateRow]:
        if not self._connected:
            logger.info("Ahrefs MCP not connected — falling back to CSV inbox.")
            return load_inbox(portal_key)
        # TODO: implement MCP calls when connector is active
        # from trendy.sources.ahrefs_mcp_impl import fetch_keywords, fetch_content
        raise NotImplementedError("Ahrefs MCP integration pending connector setup")
