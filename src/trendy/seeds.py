"""Seed kľúčovky per portál — dátovo odvodené s podporou manuálnych seedov, ktoré
žiadny auto-refresh neprepíše. Nahrádza hardcoded `PortalConfig.seed_keywords`
v config.py, ktorý slúži už len ako bootstrap fallback pre čerstvú/prázdnu DB.

Evidence sa zbiera od najsilnejšieho po najslabší signál a takto sa aj podáva LLM:
GSC dopyt > schválené témy > konkurenčné medzery (keywordy konkurencie, ktoré
nepokrývame) > naše overené Ahrefs témy > tituly článkov. LLM z toho okrem
"coverage" seedov (čo už riešime) navrhne aj "expansion" seedy — susedné témy na
rast, aby nástroj neostal len pri status quo existujúceho obsahu."""
from __future__ import annotations

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

    # Sitemap data needs no manual export (unlike Ahrefs/GSC CSVs) — fetch it
    # if we don't have any yet, so this button is useful even before the full
    # pipeline has ever run for this portal.
    has_articles = db.query(PublishedArticle).filter_by(portal_id=portal.id).first() is not None
    if not has_articles:
        try:
            from trendy.sources import sitemap
            sitemap.refresh_sitemap(portal, db, fetch_meta=True)
        except Exception as e:
            logger.warning("Sitemap refresh during seed refresh failed (non-fatal): %s", e)
            # A failed insert/flush mid-transaction leaves the session unusable
            # until rolled back — without this, the next query below raises
            # PendingRollbackError instead of the original (harmless) error.
            db.rollback()

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
    seen_norms: set[str] = set()
    for item in items[:15]:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue
        norm = slugify(kw, separator=" ", lowercase=True)
        if norm in manual_norms or norm in seen_norms:
            continue
        seen_norms.add(norm)
        # Fold the coverage/expansion type into the evidence line shown in the UI,
        # so an expansion seed is visibly flagged as a growth opportunity.
        seed_type = (item.get("type") or "").strip().lower()
        evidence = item.get("evidence") or ""
        if seed_type == "expansion":
            evidence = f"🌱 expansion — {evidence}" if evidence else "🌱 expansion"
        db.add(Seed(
            portal_id=portal.id,
            keyword=kw,
            keyword_normalized=norm,
            origin="auto",
            active=True,
            source_evidence=evidence,
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

    # Our own volume-verified topics (Ahrefs Keywords Explorer exports).
    own_rows = (
        db.query(Candidate.parent_topic)
        .filter(
            Candidate.portal_id == portal.id,
            Candidate.source.like("ahrefs_keywords%"),
            Candidate.parent_topic.isnot(None),
        )
        .order_by(Candidate.volume.desc())
        .limit(30)
        .all()
    )
    own_topics = list(dict.fromkeys(t for (t,) in own_rows if t))

    # Competitor gap = keywords a competitor ranks for (Ahrefs Site Explorer export,
    # source=ahrefs_competitors) that WE don't cover — no matched article. This is
    # the forward-looking discovery signal: topics to expand into, not just mirror.
    gap_rows = (
        db.query(Candidate.keyword, Candidate.parent_topic)
        .filter(
            Candidate.portal_id == portal.id,
            Candidate.source.like("ahrefs_competitors%"),
            Candidate.matched_article_id.is_(None),
        )
        .order_by(Candidate.volume.desc())
        .limit(40)
        .all()
    )
    competitor_gaps = list(dict.fromkeys((pt or kw) for kw, pt in gap_rows if (pt or kw)))

    return {
        "gsc_queries": gsc_queries,
        "accepted_keywords": accepted_keywords,
        "competitor_gaps": competitor_gaps,
        "own_topics": own_topics,
        "article_titles": article_titles,
    }


def _build_prompt(portal: Portal, evidence: dict[str, list[str]]) -> str:
    # Ordered strongest → weakest signal; the prompt tells the LLM to weight them
    # in this order (real demand and competitor gaps beat "what we already wrote").
    labelled = [
        ("gsc_queries", "1. REÁLNY DOPYT — top vyhľadávané frázy (GSC, 90 dní), NAJSILNEJŠÍ signál"),
        ("accepted_keywords", "2. SCHVÁLENÉ TÉMY — čo tím sám označil za relevantné"),
        ("competitor_gaps", "3. KONKURENČNÉ MEDZERY — kľúčovky, na ktoré rankuje konkurencia a MY ich nepokrývame"),
        ("own_topics", "4. NAŠE OVERENÉ TÉMY — parent topics z Ahrefs (podľa objemu)"),
        ("article_titles", "5. EXISTUJÚCI OBSAH — nedávno publikované články (najslabší signál, len kontext)"),
    ]
    sections = []
    for key, heading in labelled:
        if evidence.get(key):
            sections.append(heading + ":\n" + "\n".join(f"- {v}" for v in evidence[key]))
    evidence_text = "\n\n".join(sections)

    return f"""Si SEO stratég pre portál {portal.name}.

Nasledujúce dáta sú zoradené od NAJSILNEJŠIEHO po najslabší signál. Váž ich v tomto
poradí — reálny dopyt (GSC) a konkurenčné medzery majú prednosť pred tým, čo portál
už napísal (existujúci obsah je len kontext, nie cieľ):

{evidence_text}

Úloha: Navrhni 10-15 ŠIROKÝCH seed kľúčových fráz (2-3 slová, hlavné témy, nie dlhé
long-tail frázy). Budú vstupom pre Google Trends "rising queries" analýzu, takže
musia byť dostatočne všeobecné.

Rozlíš dva typy seedov:
- "coverage" — jadrová téma, ktorú portál už rieši a treba ju sledovať
- "expansion" — SUSEDNÁ téma, ktorá z dát logicky vyplýva (najmä z dopytu a
  konkurenčných medzier), ale portál ju ešte nepokrýva — príležitosť na rast

Cieľ aspoň 3-4 "expansion" seedy, nech nástroj neostane iba pri status quo.

Pre každý seed uveď type a stručne z čoho vychádza (evidence).

Odpovedaj IBA v JSON: [{{"keyword": "...", "type": "coverage|expansion", "evidence": "..."}}]"""


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


def build_context_string(portal_key: str, db: Session | None = None) -> str:
    """Portal context pre LLM (rss_llm, llm_probe) — odvodený z aktívnych seedov.

    Keď je dostupná DB, context sa zostaví priamo z aktívnych seedov, čo je
    vždy aktuálnejšie ako hardcoded texty. Ak DB chýba alebo je prázdna,
    padne späť na zlepšené statické opisy portálov.
    """
    if db is not None:
        portal = db.query(Portal).filter_by(key=portal_key).first()
        if portal:
            seed_rows = db.query(Seed).filter_by(portal_id=portal.id, active=True).limit(20).all()
            seed_kws = [s.keyword for s in seed_rows]
            if seed_kws:
                cfg = PORTALS.get(portal_key)
                name = cfg.name if cfg else portal_key
                return f"{name} — témy: {', '.join(seed_kws)}"

    # Vylepšené statické opisy (žiadny insultech/poisťovníctvo pre msg-life)
    defaults: dict[str, str] = {
        "msg-life": (
            "msg-life.sk — HR, employer branding, firemná kultúra, kariéra v IT, "
            "pracovný trh, zamestnávanie, benefity, leadership, onboarding, soft skills"
        ),
        "msgtester": (
            "msgtester.sk — software testing, QA engineering, test automation, "
            "manuálne testovanie, cypress, selenium, performance testing, test management"
        ),
        "msgprogramator": (
            "msgprogramator.sk — programovanie, web development, python, javascript, "
            "react, backend, frontend, DevOps, cloud, tech kariéra"
        ),
    }
    return defaults.get(portal_key, "tech and business")


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
