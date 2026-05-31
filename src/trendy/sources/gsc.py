"""GSC source — manuálny CSV import + Claude API analýza rising queries."""
from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from slugify import slugify
from sqlalchemy.orm import Session

from trendy.db import GscQuery, Portal
from trendy.config import settings
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)

# GSC CSV column names (EN and SK variants)
_COL_ALIASES = {
    "top queries": "query",
    "queries": "query",
    "query": "query",
    "keyword": "query",
    "impressions": "impressions",
    "clicks": "clicks",
    "ctr": "ctr",
    "average position": "avg_position",
    "position": "avg_position",
    "avg. position": "avg_position",
}


def _normalize_gsc_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {c: _COL_ALIASES[c] for c in df.columns if c in _COL_ALIASES}
    return df.rename(columns=rename)


def _parse_ctr(val) -> float:
    """Parse CTR: '3.5%' or 0.035 → 0.035"""
    if pd.isna(val):
        return 0.0
    s = str(val).replace("%", "").strip()
    try:
        v = float(s)
        return v / 100 if v > 1 else v
    except ValueError:
        return 0.0


def import_gsc_csv(portal: Portal, db: Session, inbox_dir: Path | None = None) -> int:
    """
    Import all GSC CSV files from data/gsc_inbox/<portal.key>/ into DB.
    Returns number of new rows inserted.
    """
    base = inbox_dir or settings.gsc_inbox_dir
    portal_dir = Path(base) / portal.key

    if not portal_dir.exists():
        logger.warning("GSC inbox not found: %s", portal_dir)
        return 0

    files = sorted(portal_dir.glob("*.csv"))
    if not files:
        logger.info("No GSC files in %s", portal_dir)
        return 0

    inserted = 0
    for fpath in files:
        export_date = _parse_date_from_filename(fpath.name)
        try:
            inserted += _import_file(fpath, portal, db, export_date)
        except Exception as e:
            logger.error("Failed to import GSC file %s: %s", fpath.name, e)

    db.commit()
    logger.info("Imported %d new GSC rows for %s", inserted, portal.key)
    return inserted


def _import_file(fpath: Path, portal: Portal, db: Session, export_date: date) -> int:
    try:
        df = pd.read_csv(fpath, encoding="utf-8-sig")
    except Exception:
        df = pd.read_csv(fpath, sep=";", encoding="utf-8-sig")

    df = _normalize_gsc_columns(df)

    if "query" not in df.columns:
        logger.warning("No query column in GSC file %s. Columns: %s", fpath.name, list(df.columns))
        return 0

    count = 0
    for _, row in df.iterrows():
        query = str(row.get("query", "")).strip()
        if not query:
            continue

        avg_pos = None
        if "avg_position" in row:
            try:
                avg_pos = float(str(row["avg_position"]).replace(",", "."))
            except (ValueError, TypeError):
                pass

        existing = db.query(GscQuery).filter_by(
            portal_id=portal.id, query=query, export_date=export_date
        ).first()

        if not existing:
            db.add(GscQuery(
                portal_id=portal.id,
                query=query,
                impressions=int(row.get("impressions", 0)) if not pd.isna(row.get("impressions", 0)) else 0,
                clicks=int(row.get("clicks", 0)) if not pd.isna(row.get("clicks", 0)) else 0,
                ctr=_parse_ctr(row.get("ctr", 0)),
                avg_position=avg_pos,
                export_date=export_date,
            ))
            count += 1

    return count


def _parse_date_from_filename(filename: str) -> date:
    """Extract date from filename: YYYY-MM-DD_queries.csv → date(...)"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return date.today()


def get_covered_queries(portal: Portal, db: Session, top_n_days: int = 90) -> dict[str, float]:
    """
    Return dict of {normalized_query: avg_position} for queries
    where portal ranks in top 20 (position ≤ 20) in recent exports.
    Used by coverage detection in pipeline.
    """
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=top_n_days)

    rows = (
        db.query(GscQuery.query, GscQuery.avg_position)
        .filter(
            GscQuery.portal_id == portal.id,
            GscQuery.export_date >= cutoff,
            GscQuery.avg_position <= 20,
        )
        .all()
    )

    result = {}
    for query, pos in rows:
        norm = slugify(query, separator=" ", lowercase=True)
        # Keep best (lowest) position if multiple exports
        if norm not in result or pos < result[norm]:
            result[norm] = pos

    return result


def get_rising_candidates_via_llm(portal_key: str, db: Session, portal: Portal) -> list[CandidateRow]:
    """
    Use Claude API to identify rising / trending queries from GSC data.
    Compares recent 30-day data vs prior 30-day data and surfaces growing queries.

    Falls back to empty list if ANTHROPIC_API_KEY not set.
    """
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — skipping LLM GSC rising analysis")
        return []

    # Build comparison dataset
    from datetime import timedelta
    today = date.today()
    recent_start = today - timedelta(days=30)
    prior_start = today - timedelta(days=60)
    prior_end = today - timedelta(days=31)

    def _get_period(start: date, end: date):
        rows = (
            db.query(GscQuery.query, GscQuery.impressions, GscQuery.avg_position)
            .filter(
                GscQuery.portal_id == portal.id,
                GscQuery.export_date >= start,
                GscQuery.export_date <= end,
            )
            .all()
        )
        agg: dict[str, dict] = {}
        for q, imp, pos in rows:
            if q not in agg:
                agg[q] = {"impressions": 0, "positions": []}
            agg[q]["impressions"] += imp or 0
            if pos:
                agg[q]["positions"].append(pos)
        return {q: {"impressions": v["impressions"], "avg_pos": sum(v["positions"]) / len(v["positions"]) if v["positions"] else None} for q, v in agg.items()}

    recent = _get_period(recent_start, today)
    prior = _get_period(prior_start, prior_end)

    if not recent:
        return []

    # Build summary for Claude
    rising_raw = []
    for q, r in recent.items():
        p = prior.get(q, {"impressions": 0})
        growth = r["impressions"] - p["impressions"]
        if growth > 0:
            rising_raw.append({"query": q, "recent_imp": r["impressions"], "prior_imp": p["impressions"], "growth": growth})

    rising_raw.sort(key=lambda x: x["growth"], reverse=True)
    top50 = rising_raw[:50]

    if not top50:
        return []

    lines = "\n".join(
        f"- {r['query']} (recent: {r['recent_imp']} impr, prior: {r['prior_imp']} impr, +{r['growth']})"
        for r in top50
    )

    prompt = f"""Analyzuješ GSC dáta portálu {portal.name}.
Nasledujúce queries zaznamenali nárast impressií za posledných 30 dní oproti predchádzajúcim 30 dňom:

{lines}

Úloha: Identifikuj z tohto zoznamu maximálne 15 queries ktoré reprezentujú skutočne rastúce tematické okruhy (nie len sezónne šumy, brandové queries, alebo navigačné). Pre každý uveď prečo je zaujímavý ako téma pre obsah.

Odpovedaj v JSON formáte: [{{"keyword": "...", "reason": "...", "potential": "high|medium|low"}}]
Iba JSON, žiadny iný text."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = msg.content[0].text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        items = json.loads(text)

        candidates = []
        for item in items:
            kw = item.get("keyword", "").strip()
            if not kw:
                continue
            candidates.append(CandidateRow(
                keyword=kw,
                keyword_normalized=slugify(kw, separator=" ", lowercase=True),
                source="gsc_rising_llm",
                extra={"reason": item.get("reason"), "potential": item.get("potential")},
            ))
        logger.info("GSC LLM analysis found %d rising candidates for %s", len(candidates), portal_key)
        return candidates
    except Exception as e:
        logger.error("GSC LLM rising analysis failed: %s", e)
        return []
