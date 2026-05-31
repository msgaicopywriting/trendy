"""Pipeline orchestrátor — 7 krokov metodiky per portál."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, date

from sqlalchemy.orm import Session

from trendy.config import PORTALS, settings
from trendy.db import (
    get_db, Portal, Candidate, PipelineRun, PublishedArticle,
)
from trendy.sources.base import CandidateRow
from trendy.sources import ahrefs, gsc, clusters, trends, sitemap
from trendy.sources.reddit import fetch_trending as reddit_fetch
from trendy.sources.rss import fetch_rss_candidates
from trendy.sources.llm_probe import fetch_claude_probe, fetch_perplexity_probe
from trendy.scoring import compute_score, ScoringInput
from trendy.lifecycle import is_suppressed, apply_lifecycle_filter, handle_returned_from_cooldown
from slugify import slugify

logger = logging.getLogger(__name__)


def run_pipeline(portal_key: str, db: Session | None = None) -> dict:
    """
    Execute full 7-step pipeline for one portal.
    Returns summary dict with counts.
    """
    cfg = PORTALS.get(portal_key)
    if not cfg:
        raise ValueError(f"Unknown portal key: {portal_key}")

    own_db = db is None
    db = db or get_db()

    portal = db.query(Portal).filter_by(key=portal_key).first()
    if not portal:
        raise RuntimeError(f"Portal '{portal_key}' not in DB. Run init_db() first.")

    run = PipelineRun(portal_id=portal.id)
    db.add(run)
    db.flush()
    run_id = run.id
    logger.info("=== Pipeline START: %s (run_id=%d) ===", portal_key, run_id)

    try:
        # ─── Krok 1: Inventár publikovaných tém (sitemap) ─────────────────
        logger.info("[1/7] Sitemap refresh")
        try:
            sitemap.refresh_sitemap(portal, db, fetch_meta=True)
        except Exception as e:
            logger.warning("Sitemap refresh failed (non-fatal): %s", e)

        # ─── Krok 2: Inventár pokrytia v GSC ──────────────────────────────
        logger.info("[2/7] GSC import + coverage map")
        try:
            gsc.import_gsc_csv(portal, db)
        except Exception as e:
            logger.warning("GSC import failed (non-fatal): %s", e)

        covered_queries = gsc.get_covered_queries(portal, db)
        covered_slugs = sitemap.get_covered_slugs(portal, db)
        logger.info("Coverage: %d GSC queries, %d sitemap slugs", len(covered_queries), len(covered_slugs))

        # ─── Krok 3: Kandidátsky pool (všetky zdroje) ─────────────────────
        logger.info("[3/7] Fetching candidates from all sources")
        all_candidates: list[CandidateRow] = []

        # Ahrefs (CSV inbox, falls back gracefully)
        all_candidates.extend(ahrefs.load_inbox(portal_key))

        # GSC rising via Claude LLM
        all_candidates.extend(gsc.get_rising_candidates_via_llm(portal_key, db, portal))

        # Reddit
        all_candidates.extend(reddit_fetch(portal_key))

        # RSS + Claude
        all_candidates.extend(fetch_rss_candidates(portal_key))

        # LLM probing
        all_candidates.extend(fetch_claude_probe(portal_key))
        all_candidates.extend(fetch_perplexity_probe(portal_key))

        logger.info("Total raw candidates: %d", len(all_candidates))

        # ─── Krok 4: Google Trends signál ─────────────────────────────────
        logger.info("[4/7] Google Trends enrichment")
        # Only fetch trends for candidates that pass volume threshold
        threshold = cfg.volume_threshold
        enrichable = [c for c in all_candidates if c.volume >= threshold]

        trend_data: dict = {}
        if enrichable:
            keywords_to_probe = list({c.keyword for c in enrichable})[:50]
            try:
                trend_data = trends.fetch_trend_data(keywords_to_probe)
            except Exception as e:
                logger.warning("pytrends fetch failed (non-fatal): %s", e)

        # Also fetch rising queries from seed keywords
        rising_from_trends: list[CandidateRow] = []
        for seed in cfg.seed_keywords[:5]:  # limit to 5 seeds to avoid rate limits
            try:
                rising_from_trends.extend(trends.fetch_rising_queries(seed))
            except Exception as e:
                logger.warning("pytrends rising for '%s' failed: %s", seed, e)

        all_candidates.extend(rising_from_trends)
        logger.info("pytrends added %d rising candidates", len(rising_from_trends))

        # ─── Krok 5: Scoring ───────────────────────────────────────────────
        logger.info("[5/7] Scoring")
        candidates_found = 0
        candidates_suppressed = 0

        for row in all_candidates:
            if not row.keyword or not row.keyword_normalized:
                continue

            # Volume threshold filter (skip very low volume from Ahrefs; LLM/RSS have no volume)
            if row.volume > 0 and row.volume < threshold:
                continue

            # Check for existing DB candidate
            existing = db.query(Candidate).filter_by(
                portal_id=portal.id,
                keyword_normalized=row.keyword_normalized,
            ).first()

            # Lifecycle suppression check
            if existing:
                suppressed, reason = is_suppressed(existing)
                if suppressed:
                    db.add(__import__("trendy.db", fromlist=["SuppressedCandidate"]).SuppressedCandidate(
                        run_id=run_id,
                        candidate_id=existing.id,
                        suppressed_reason=reason,
                    ))
                    candidates_suppressed += 1
                    continue

            # GSC coverage
            gsc_pos = covered_queries.get(row.keyword_normalized)
            has_article = row.keyword_normalized in covered_slugs or any(
                slug in row.keyword_normalized or row.keyword_normalized in slug
                for slug in covered_slugs
            )

            # Trend data from pytrends
            tdata = trend_data.get(row.keyword, {})
            mom_pct = tdata.get("mom_pct", 0.0)
            yoy_pct = tdata.get("yoy_pct", 0.0)
            trend_json = json.dumps(tdata.get("timeline", {}))

            # Cluster assignment
            cluster = row.cluster or clusters.assign_cluster(row.keyword_normalized, portal_key)

            # Effective volume: keep the best known volume so a volume-less discovery
            # re-fetch never erases a real Ahrefs volume, and a real Ahrefs volume closes
            # the loop on a needs_volume topic. needs_volume = discovery topic still unverified.
            is_ahrefs = (row.source or "").startswith("ahrefs")
            prior_volume = existing.volume if existing else 0
            effective_volume = max(row.volume, prior_volume or 0)
            needs_volume = effective_volume == 0 and not is_ahrefs
            effective_kd = row.kd if row.kd is not None else (existing.kd if existing else None)

            # Compute score
            result = compute_score(ScoringInput(
                volume=effective_volume,
                kd=effective_kd,
                intent=row.intent,
                gsc_avg_position=gsc_pos,
                gsc_impressions=0,
                trend_mom_pct=mom_pct,
                trend_yoy_pct=yoy_pct,
                has_published_article=has_article,
                days_since_first_seen=0 if not existing else (
                    (datetime.now(timezone.utc) - existing.first_seen).days
                    if existing.first_seen.tzinfo
                    else (datetime.now(timezone.utc) - existing.first_seen.replace(tzinfo=timezone.utc)).days
                ),
                portal_cfg=cfg,
                source=row.source,
            ))

            if existing:
                # Update scoring fields
                handle_returned_from_cooldown(existing, result.growth_score)
                existing.volume = effective_volume
                existing.kd = effective_kd
                existing.needs_volume = needs_volume
                existing.trend_score = result.trend_score
                existing.volume_score = result.volume_score
                existing.growth_score = result.growth_score
                existing.gap_score = result.gap_score
                existing.opportunity_score = result.opportunity_score
                existing.tag = result.tag
                existing.trend_data_json = trend_json
                existing.trend_mom_pct = mom_pct
                existing.trend_yoy_pct = yoy_pct
                existing.gsc_avg_position = gsc_pos
                existing.cluster = cluster or existing.cluster
                existing.last_scored = datetime.now(timezone.utc)
                if existing.status == "seen":
                    pass  # keep seen
                elif existing.status == "new":
                    existing.status = "seen"
            else:
                # New candidate
                new_c = Candidate(
                    portal_id=portal.id,
                    keyword=row.keyword,
                    keyword_normalized=row.keyword_normalized,
                    parent_topic=row.parent_topic,
                    cluster=cluster,
                    volume=effective_volume,
                    kd=effective_kd,
                    intent=row.intent,
                    source=row.source,
                    needs_volume=needs_volume,
                    trend_score=result.trend_score,
                    volume_score=result.volume_score,
                    growth_score=result.growth_score,
                    gap_score=result.gap_score,
                    opportunity_score=result.opportunity_score,
                    tag=result.tag,
                    trend_data_json=trend_json,
                    trend_mom_pct=mom_pct,
                    trend_yoy_pct=yoy_pct,
                    gsc_avg_position=gsc_pos,
                    status="new",
                    ahrefs_import_file=row.extra.get("file"),
                )
                db.add(new_c)
                candidates_found += 1

        db.flush()

        # ─── Krok 6: Záver behu ────────────────────────────────────────────
        logger.info("[6/7] Finalizing run")
        run.candidates_found = candidates_found
        run.candidates_suppressed = candidates_suppressed
        run.finished_at = datetime.now(timezone.utc)
        run.status = "completed"
        db.commit()

        summary = {
            "run_id": run_id,
            "portal": portal_key,
            "candidates_found": candidates_found,
            "candidates_suppressed": candidates_suppressed,
            "status": "completed",
        }
        logger.info("=== Pipeline DONE: %s — found=%d, suppressed=%d ===",
                    portal_key, candidates_found, candidates_suppressed)
        return summary

    except Exception as e:
        logger.error("Pipeline FAILED for %s: %s", portal_key, e, exc_info=True)
        run.status = "failed"
        run.error_message = str(e)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    finally:
        if own_db:
            db.close()
