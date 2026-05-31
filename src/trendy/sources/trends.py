"""Google Trends source — pytrends wrapper s rate limitingom."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from slugify import slugify

from trendy.config import settings
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)


def _get_pytrends():
    from pytrends.request import TrendReq
    return TrendReq(
        hl=settings.pytrends_language,
        tz=60,  # UTC+1 (SK)
        geo=settings.pytrends_geo,
    )


def fetch_trend_data(keywords: list[str], timeframe: str = "today 12-m") -> dict[str, Any]:
    """
    Fetch 12-month interest-over-time for up to 5 keywords.
    Returns dict: {keyword: {"timeline": {date: value}, "yoy_pct": float, "mom_pct": float}}
    """
    if not keywords:
        return {}

    # pytrends max 5 at a time
    results = {}
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        try:
            results.update(_fetch_batch(batch, timeframe))
        except Exception as e:
            logger.warning("pytrends batch failed for %s: %s", batch, e)
        if i + 5 < len(keywords):
            time.sleep(settings.pytrends_rate_limit_seconds)

    return results


def _fetch_batch(keywords: list[str], timeframe: str) -> dict[str, Any]:
    pt = _get_pytrends()
    pt.build_payload(keywords, timeframe=timeframe, geo=settings.pytrends_geo)
    time.sleep(settings.pytrends_rate_limit_seconds)

    df = pt.interest_over_time()
    if df is None or df.empty:
        return {}

    result = {}
    for kw in keywords:
        if kw not in df.columns:
            continue
        series = df[kw].dropna()
        if series.empty:
            continue

        timeline = {str(d.date()): int(v) for d, v in series.items()}

        # MoM: last month vs month before
        vals = series.values
        mom_pct = _safe_growth(vals[-1], vals[-5]) if len(vals) >= 5 else 0.0
        # YoY: last 4 weeks avg vs same 4 weeks last year
        yoy_pct = _safe_growth(vals[-1:].mean(), vals[-53:-49].mean()) if len(vals) >= 53 else 0.0

        result[kw] = {
            "timeline": timeline,
            "yoy_pct": round(yoy_pct, 1),
            "mom_pct": round(mom_pct, 1),
        }

    return result


def _safe_growth(current, prior) -> float:
    try:
        if prior == 0:
            return 100.0 if current > 0 else 0.0
        return ((float(current) - float(prior)) / float(prior)) * 100
    except Exception:
        return 0.0


def fetch_rising_queries(seed_keyword: str, geo: str | None = None) -> list[CandidateRow]:
    """
    Fetch 'Rising related queries' from Google Trends for a seed keyword.
    These are emerging terms not yet in Ahrefs with measurable trendovosť.
    """
    geo = geo or settings.pytrends_geo
    try:
        pt = _get_pytrends()
        pt.build_payload([seed_keyword], timeframe="today 3-m", geo=geo)
        time.sleep(settings.pytrends_rate_limit_seconds)

        related = pt.related_queries()
        rising_df = related.get(seed_keyword, {}).get("rising")
        if rising_df is None or rising_df.empty:
            return []

        candidates = []
        for _, row in rising_df.iterrows():
            query = str(row.get("query", "")).strip()
            value = int(row.get("value", 0))
            if not query:
                continue
            candidates.append(CandidateRow(
                keyword=query,
                keyword_normalized=slugify(query, separator=" ", lowercase=True),
                source="pytrends_rising",
                extra={"seed_keyword": seed_keyword, "rising_value": value},
            ))

        logger.info("pytrends rising queries for '%s': %d found", seed_keyword, len(candidates))
        return candidates

    except Exception as e:
        logger.warning("pytrends rising queries failed for '%s': %s", seed_keyword, e)
        return []
