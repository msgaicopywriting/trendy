"""Seed kľúčovky per portál — dátovo odvodené (GSC, sitemap, accepted candidates,
Ahrefs parent topics) s podporou manuálnych seedov, ktoré žiadny auto-refresh
neprepíše. Nahrádza hardcoded `PortalConfig.seed_keywords` v config.py, ktorý
slúži už len ako bootstrap fallback pre čerstvú/prázdnu DB."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from slugify import slugify
from sqlalchemy import func
from sqlalchemy.orm import Session

from trendy.config import PORTALS
from trendy.db import Candidate, GscQuery, Portal, PublishedArticle, Seed
from trendy.llm import llm_available, llm_complete, parse_json_block

logger = logging.getLogger(__name__)

_ORIGIN_PRIORITY = {"manual": 0, "auto": 1, "bootstrap": 2}
_EVIDENCE_WINDOW_DAYS = 90


def get_active_seeds(portal: Portal, db: Session) -> list[str]:
    """Active seeds for a portal, manual first. Bootstraps from config.py's
    hardcoded list on first use (empty DB) so the pipeline never has zero seeds."""
    existing = db.query(Seed).filter_by(portal_id=portal.id, active=True).all()
    if not existing:
        cfg = PORTALS.get(portal.key)
        if cfg:
            _bootstrap_seeds(portal, db, cfg.seed_keywords)
            existing = db.query(Seed).filter_by(portal_id=portal.id, active=True).all()

    existing.sort(key=lambda s: _ORIGIN_PRIORITY.get(s.origin, 3))
    return [s.keyword for s in existing]


def _bootstrap_seeds(portal: Portal, db: Session, seed_keywords: list[str]) -> None:
    for kw in seed_keywords:
        norm = slugify(kw, separator=" ", lowercase=True)
        if db.query(Seed).filter_by(portal_id=portal.id, keyword_normalized=norm).first():
            continue
        db.add(Seed(
            portal_id=portal.id,
            keyword=kw,
            keyword_normalized=norm,
            origin="bootstrap",
            active=True,
        ))
    db.commit()


def refresh_auto_seeds(portal: Portal, db: Session) -> dict:
    """
    Re-derive auto seeds from real portal data via the LLM. Manual seeds are
    never touched; new auto seeds that collide with a manual keyword are
    dropped in favor of the manual one.
    """
    if not llm_available():
        return {"status": "skipped", "reason": "LLM not available"}

    evidence = _collect_evidence(portal, db)
    if not any(evidence.values()):
        return {"status": "skipped", "reason": "no evidence data (GSC/sitemap/candidates all empty)"}

    text = llm_complete(_build_prompt(portal, evidence), max_tokens=2048, json_output=True)
    items = parse_json_block(text)
    if not isinstance(items, list) or not items:
        return {"status": "skipped", "reason": "LLM returned no parseable seeds"}

    manual_norms = {
        s.keyword_normalized
        for s in db.query(Seed).filter_by(portal_id=portal.id, origin="manual").all()
    }

    db.query(Seed).filter(
        Seed.portal_id == portal.id,
        Seed.origin.in_(["auto", "bootstrap"]),
    ).delete(synchronize_session=False)

    added = 0
    for item in items[:15]:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue
        norm = slugify(kw, separator=" ", lowercase=True)
        if norm in manual_norms:
            continue
        db.add(Seed(
            portal_id=portal.id,
            keyword=kw,
            keyword_normalized=norm,
            origin="auto",
            active=True,
            source_evidence=json.dumps(item.get("evidence") or ""),
        ))
        added += 1

    db.commit()
    logger.info("refresh_auto_seeds: %d new auto seeds for %s", added, portal.key)
    return {"status": "ok", "added": added}


def _collect_evidence(portal: Portal, db: Session) -> dict[str, list[str]]:
    cutoff = date.today() - timedelta(days=_EVIDENCE_WINDOW_DAYS)

    gsc_rows = (
        db.query(GscQuery.query, func.sum(GscQuery.impressions).label("total_impr"))
        .filter(GscQuery.portal_id == portal.id, GscQuery.export_date >= cutoff)
        .group_by(GscQuery.query)
        .order_by(func.sum(GscQuery.impressions).desc())
        .limit(50)
        .all()
    )
    gsc_queries = [q for q, _ in gsc_rows]

    article_rows = (
        db.query(PublishedArticle.title)
        .filter(PublishedArticle.portal_id == portal.id, PublishedArticle.title.isnot(None))
        .order_by(PublishedArticle.last_seen.desc())
        .limit(100)
        .all()
    )
    article_titles = [t for (t,) in article_rows if t]

    accepted_rows = (
        db.query(Candidate.keyword, Candidate.cluster)
        .filter(Candidate.portal_id == portal.id, Candidate.status.in_(["accepted", "in_progress", "published"]))
        .limit(100)
        .all()
    )
    accepted_keywords = [f"{kw} ({cluster})" if cluster else kw for kw, cluster in accepted_rows]

    ahrefs_rows = (
        db.query(Candidate.parent_topic)
        .filter(
            Candidate.portal_id == portal.id,
            Candidate.source.like("ahrefs%"),
            Candidate.parent_topic.isnot(None),
        )
        .order_by(Candidate.volume.desc())
        .limit(30)
        .all()
    )
    ahrefs_topics = list(dict.fromkeys(t for (t,) in ahrefs_rows if t))

    return {
        "gsc_queries": gsc_queries,
        "article_titles": article_titles,
        "accepted_keywords": accepted_keywords,
        "ahrefs_topics": ahrefs_topics,
    }


def _build_prompt(portal: Portal, evidence: dict[str, list[str]]) -> str:
    sections = []
    if evidence["gsc_queries"]:
        sections.append(
            "Top vyhľadávané frázy (GSC, posledných 90 dní):\n"
            + "\n".join(f"- {q}" for q in evidence["gsc_queries"])
        )
    if evidence["article_titles"]:
        sections.append(
            "Nedávno publikované články:\n"
            + "\n".join(f"- {t}" for t in evidence["article_titles"])
        )
    if evidence["accepted_keywords"]:
        sections.append(
            "Akceptované/publikované témy z pipeline:\n"
            + "\n".join(f"- {k}" for k in evidence["accepted_keywords"])
        )
    if evidence["ahrefs_topics"]:
        sections.append(
            "Parent topics z Ahrefs (podľa objemu):\n"
            + "\n".join(f"- {t}" for t in evidence["ahrefs_topics"])
        )
    evidence_text = "\n\n".join(sections)

    return f"""Si SEO stratég pre portál {portal.name}.

Nasledujúce dáta popisujú, o čom portál reálne píše a čo ľudia hľadajú:

{evidence_text}

Úloha: Vydestiluj z týchto dát 10-15 ŠIROKÝCH seed kľúčových fráz (2-3 slová, hlavné
témy, nie dlhé long-tail frázy), ktoré pokrývajú hlavné tematické okruhy portálu.
Tieto seedy budú vstupom pre Google Trends "rising queries" analýzu, takže musia
byť dostatočne široké/všeobecné.

Pre každý seed uveď, z ktorého vstupu (GSC / články / akceptované témy / Ahrefs)
vychádza.

Odpovedaj IBA v JSON: [{{"keyword": "...", "evidence": "..."}}]"""


def add_manual_seed(portal: Portal, keyword: str, db: Session) -> Seed:
    """Add (or promote to manual) a user-supplied seed. Manual ownership wins
    over any existing auto/bootstrap seed with the same normalized keyword."""
    norm = slugify(keyword, separator=" ", lowercase=True)
    existing = db.query(Seed).filter_by(portal_id=portal.id, keyword_normalized=norm).first()
    if existing:
        existing.origin = "manual"
        existing.active = True
        existing.keyword = keyword
        db.commit()
        return existing

    seed = Seed(
        portal_id=portal.id,
        keyword=keyword,
        keyword_normalized=norm,
        origin="manual",
        active=True,
    )
    db.add(seed)
    db.commit()
    return seed


def remove_seed(seed_id: int, db: Session) -> None:
    db.query(Seed).filter_by(id=seed_id).delete()
    db.commit()


def set_seed_active(seed_id: int, active: bool, db: Session) -> None:
    seed = db.query(Seed).filter_by(id=seed_id).first()
    if seed:
        seed.active = active
        db.commit()


def select_seeds_for_run(seeds: list[str], run_index: int, batch: int = 5) -> list[str]:
    """
    Google Trends allows only ~5 seeds per run. With more seeds than that,
    rotate a deterministic window by run_index so every seed gets a turn
    across successive pipeline runs instead of only the first `batch` ever
    being probed.
    """
    if len(seeds) <= batch:
        return seeds
    start = (run_index * batch) % len(seeds)
    end = start + batch
    if end <= len(seeds):
        return seeds[start:end]
    return seeds[start:] + seeds[:end - len(seeds)]
