"""
Briefing handoff — generuje podklady pre brief skilly per portál.

Každý portál má svoju schému polí definovanú v PORTAL_BRIEF_SCHEMAS.
Výstup: XLSX + JSON so štruktúrovanými podkladmi.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from trendy.db import get_db, Candidate, Portal, PublishedArticle, GscQuery
from trendy.config import PORTALS

logger = logging.getLogger(__name__)


# Per-portál mapovanie polí → názvy stĺpcov vo výstupnom XLSX
# (prispôsobí sa podľa schémy konkrétneho brief skillu)
PORTAL_BRIEF_SCHEMAS: dict[str, dict] = {
    "msg-life": {
        "sheet_name": "Brief podklady",
        "fields": {
            "keyword":           "Hlavná kľúčovka",
            "parent_topic":      "Parent topic",
            "cluster":           "Klaster",
            "volume":            "Search volume (SK/mes)",
            "kd":                "KD (Ahrefs)",
            "intent":            "Intent",
            "trend_mom_pct":     "Trend M/M (%)",
            "trend_yoy_pct":     "Trend R/R (%)",
            "gsc_avg_position":  "GSC avg. pozícia",
            "tag":               "Typ príležitosti",
            "trend_score":       "TrendScore",
            "source":            "Zdroj dát",
            "related_queries":   "Related / Rising queries",
            "portal_url":        "Portál URL",
            "similar_articles":  "Podobné existujúce články",
            "brief_notes":       "Poznámky pre copywritera",
        },
    },
    "msgtester": {
        "sheet_name": "Brief podklady",
        "fields": {
            "keyword":           "Hlavná kľúčovka",
            "parent_topic":      "Parent topic",
            "cluster":           "Klaster",
            "volume":            "Search volume (/mes)",
            "kd":                "KD",
            "intent":            "Intent",
            "trend_mom_pct":     "Trend M/M (%)",
            "tag":               "Typ príležitosti",
            "trend_score":       "TrendScore",
            "source":            "Zdroj",
            "related_queries":   "Related queries",
            "portal_url":        "Portál URL",
            "similar_articles":  "Súvisiace články",
            "brief_notes":       "Poznámky",
        },
    },
    "msgprogramator": {
        "sheet_name": "Brief podklady",
        "fields": {
            "keyword":           "Hlavná kľúčovka",
            "parent_topic":      "Parent topic",
            "cluster":           "Klaster",
            "volume":            "Search volume (/mes)",
            "kd":                "KD",
            "intent":            "Intent",
            "trend_mom_pct":     "Trend M/M (%)",
            "trend_yoy_pct":     "Trend R/R (%)",
            "tag":               "Typ príležitosti",
            "trend_score":       "TrendScore",
            "source":            "Zdroj",
            "related_queries":   "Related queries",
            "portal_url":        "Portál URL",
            "similar_articles":  "Súvisiace články",
            "brief_notes":       "Poznámky",
        },
    },
}


def build_brief_data(candidate_id: int, db: Session | None = None) -> dict:
    """
    Zostaví dict so všetkými podkladmi pre brief.
    Volá sa interne aj pri JSON exporte.
    """
    own_db = db is None
    db = db or get_db()

    try:
        c = db.get(Candidate, candidate_id)
        if not c:
            raise ValueError(f"Candidate {candidate_id} not found")

        portal = db.get(Portal, c.portal_id)
        cfg = PORTALS.get(portal.key)

        # Related queries from trend data JSON
        related_queries = []
        if c.trend_data_json:
            try:
                tdata = json.loads(c.trend_data_json)
                # If it contains related_rising key (future enrichment)
                related_queries = tdata.get("related_rising", [])
            except Exception:
                pass

        # Similar published articles
        similar_articles = []
        if c.matched_article_id:
            art = db.query(PublishedArticle).get(c.matched_article_id)
            if art:
                similar_articles.append(f"{art.title or ''} — {art.url}")
        else:
            fuzzy_arts = (
                db.query(PublishedArticle)
                .filter_by(portal_id=c.portal_id)
                .filter(PublishedArticle.slug_normalized.contains(
                    c.keyword_normalized.split()[0] if c.keyword_normalized else ""
                ))
                .limit(5)
                .all()
            )
            similar_articles = [f"{a.title or ''} — {a.url}" for a in fuzzy_arts]

        return {
            "keyword":          c.keyword,
            "parent_topic":     c.parent_topic or "",
            "cluster":          c.cluster or "",
            "volume":           c.volume or 0,
            "kd":               c.kd if c.kd is not None else "",
            "intent":           c.intent or "",
            "trend_mom_pct":    round(c.trend_mom_pct or 0, 1),
            "trend_yoy_pct":    round(c.trend_yoy_pct or 0, 1),
            "gsc_avg_position": round(c.gsc_avg_position, 1) if c.gsc_avg_position else "",
            "tag":              c.tag or "",
            "trend_score":      round(c.trend_score or 0, 1),
            "source":           c.source or "",
            "related_queries":  "; ".join(related_queries) if related_queries else "",
            "portal_url":       cfg.url if cfg else "",
            "similar_articles": "\n".join(similar_articles),
            "brief_notes":      _auto_brief_notes(c),
            # Meta
            "portal_key":       portal.key,
            "portal_name":      portal.name,
            "export_date":      date.today().isoformat(),
            "candidate_id":     c.id,
        }
    finally:
        if own_db:
            db.close()


def export_candidate_brief(candidate_id: int, db: Session | None = None) -> bytes:
    """
    Generuje XLSX s podkladmi pre brief skill daného portálu.
    Vracia bytes (vhodné pre Streamlit download_button).
    """
    data = build_brief_data(candidate_id, db)
    portal_key = data["portal_key"]
    schema = PORTAL_BRIEF_SCHEMAS.get(portal_key, PORTAL_BRIEF_SCHEMAS["msg-life"])
    fields = schema["fields"]
    sheet_name = schema["sheet_name"]

    # Build single-row DataFrame with renamed columns
    row = {display_name: data.get(field_key, "") for field_key, display_name in fields.items()}
    df = pd.DataFrame([row])

    # Write to buffer
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.T.reset_index().rename(columns={"index": "Pole", 0: "Hodnota"}).to_excel(
            writer, sheet_name=sheet_name, index=False
        )
        # Auto-width columns
        ws = writer.sheets[sheet_name]
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 80)

    buf.seek(0)
    logger.info("Brief export generated for candidate %d (%s)", candidate_id, data["keyword"])
    return buf.getvalue()


def export_candidate_brief_json(candidate_id: int, db: Session | None = None) -> str:
    """Generuje JSON s podkladmi — vhodné pre API alebo brief skill integration."""
    data = build_brief_data(candidate_id, db)
    return json.dumps(data, ensure_ascii=False, indent=2)


def export_multiple_briefs(candidate_ids: list[int], db: Session | None = None) -> bytes:
    """
    Bulk export — viacero kandidátov do jedného XLSX (každý ako row).
    """
    own_db = db is None
    db = db or get_db()

    try:
        rows = []
        for cid in candidate_ids:
            try:
                data = build_brief_data(cid, db)
                portal_key = data["portal_key"]
                schema = PORTAL_BRIEF_SCHEMAS.get(portal_key, PORTAL_BRIEF_SCHEMAS["msg-life"])
                row = {display_name: data.get(field_key, "")
                       for field_key, display_name in schema["fields"].items()}
                row["Portál"] = data["portal_name"]
                rows.append(row)
            except Exception as e:
                logger.warning("Skipping candidate %d: %s", cid, e)

        if not rows:
            raise ValueError("No valid candidates to export")

        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Brief podklady")
            ws = writer.sheets["Brief podklady"]
            for col in ws.columns:
                max_len = max((len(str(cell.value or "")) for cell in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        buf.seek(0)
        return buf.getvalue()
    finally:
        if own_db:
            db.close()


def _auto_brief_notes(c: Candidate) -> str:
    """Automaticky generované poznámky pre copywritera na základe scoring dát."""
    notes = []

    if c.tag == "rising":
        notes.append(f"🚀 Rastúca téma — trend M/M: {c.trend_mom_pct:+.0f}%. Odporúčame publikovať čo najskôr.")
    elif c.tag == "newly_discovered":
        notes.append("✨ Nová emerging téma — nízka konkurencia, window of opportunity.")
    elif c.tag == "gap":
        notes.append("🎯 Content gap — portál na túto tému neranku je. Prioritizuj štruktúru a E-E-A-T.")
    elif c.tag == "refresh":
        notes.append(f"♻️ Refresh kandidát — existujúci článok ranku na poz. {c.gsc_avg_position:.0f}. Aktualizuj obsah.")

    if c.kd is not None:
        if c.kd < 20:
            notes.append(f"KD={c.kd} — veľmi nízka obtiažnosť, dobrá šanca na rýchly rank.")
        elif c.kd > 60:
            notes.append(f"KD={c.kd} — vysoká obtiažnosť. Potrebný silný E-E-A-T a spätné odkazy.")

    if c.intent:
        notes.append(f"Intent: {c.intent} — prispôsob formát obsahu (napr. how-to, list, pilier stránka).")

    return " | ".join(notes) if notes else "—"
