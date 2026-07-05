"""Google Trends source — trendspy wrapper (pytrends nahradený, je nemaintainovaný
a Google ho čoraz agresívnejšie blokuje 429-kami)."""
from __future__ import annotations

import logging
from typing import Any

from slugify import slugify

from trendy.config import settings
from trendy.llm import llm_available, llm_complete, parse_json_block
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)

_PORTAL_CONTEXT = {
    "msg-life": "HR, employer branding, kariéra, insurtech, poisťovníctvo, Slovak market",
    "msgtester": "software testing, QA, automatizácia testov, test engineering",
    "msgprogramator": "programovanie, software development, tech kariéra",
}


def _get_trends_client():
    from trendspy import Trends
    return Trends(
        language=settings.pytrends_language,
        tzs=60,  # UTC+1 (SK)
        request_delay=settings.pytrends_rate_limit_seconds,
    )


def fetch_trend_data(keywords: list[str], timeframe: str = "today 12-m") -> dict[str, Any]:
    """
    Fetch 12-month interest-over-time for up to 5 keywords.
    Returns dict: {keyword: {"timeline": {date: value}, "yoy_pct": float, "mom_pct": float}}
    """
    if not keywords:
        return {}

    tr = _get_trends_client()

    # Google Trends compare tool caps at 5 keywords per request
    results = {}
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        try:
            results.update(_fetch_batch(tr, batch, timeframe))
        except Exception as e:
            logger.warning("trendspy batch failed for %s: %s", batch, e)

    return results


def _fetch_batch(tr, keywords: list[str], timeframe: str) -> dict[str, Any]:
    df = tr.interest_over_time(keywords, timeframe=timeframe, geo=settings.pytrends_geo)
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

        # MoM: last 4 weeks avg vs prior 4 weeks avg (4-week windows to smooth weekly noise)
        vals = series.values
        mom_pct = _safe_growth(vals[-4:].mean(), vals[-8:-4].mean()) if len(vals) >= 8 else 0.0
        # YoY: last 4 weeks avg vs first 4 weeks of the 12-month window (~1 year ago)
        yoy_pct = _safe_growth(vals[-4:].mean(), vals[:4].mean()) if len(vals) >= 48 else 0.0

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
        tr = _get_trends_client()
        related = tr.related_queries(seed_keyword, timeframe="today 3-m", geo=geo)
        rising_df = related.get("rising")
        if rising_df is None or rising_df.empty:
            return []

        candidates = []
        for _, row in rising_df.iterrows():
            query = str(row.get("query", "")).strip()
            try:
                value = int(row.get("value", 0))
            except (TypeError, ValueError):
                value = 0
            if not query:
                continue
            candidates.append(CandidateRow(
                keyword=query,
                keyword_normalized=slugify(query, separator=" ", lowercase=True),
                # Tag kept as "pytrends_rising" for continuity with existing DB rows and
                # scoring._classify() special-casing, even though the library backing it changed.
                source="pytrends_rising",
                extra={"seed_keyword": seed_keyword, "rising_value": value},
            ))

        logger.info("Google Trends rising queries for '%s': %d found", seed_keyword, len(candidates))
        return candidates

    except Exception as e:
        logger.warning("Google Trends rising queries failed for '%s': %s", seed_keyword, e)
        return []


def fetch_trending_now(portal_key: str) -> list[CandidateRow]:
    """
    Google Trends 'Trending Now' RSS feed — breakout SK search terms from the last
    ~24-48h (a days-scale signal, unlike the months-scale fetch_rising_queries).
    Free, no API key.

    The raw feed is nationwide, not portal-specific, so most of it is noise for a
    niche B2B portal (sports, weather, celebrities). An LLM filters it down to
    relevant items — mirrors the RSS summarization pattern in sources/rss.py.
    Without an LLM key there's no reliable way to filter the noise, so it's
    skipped entirely rather than polluting candidates with irrelevant trends.
    """
    if not llm_available():
        logger.info("Trending Now RSS: LLM not available, skipping (feed too broad to use unfiltered)")
        return []

    try:
        tr = _get_trends_client()
        items = tr.trending_now_by_rss(geo=settings.pytrends_geo)
    except Exception as e:
        logger.warning("trendspy trending_now_by_rss failed: %s", e)
        return []

    if not items:
        return []

    titles_text = "\n".join(f"- {item.keyword}" for item in items[:60] if item.keyword)
    if not titles_text:
        return []

    context = _PORTAL_CONTEXT.get(portal_key, "tech a business")
    prompt = f"""Si SEO analytik pre portál zameraný na: {context}.

Toto sú aktuálne trendujúce vyhľadávania na Slovensku za posledných ~24-48 hodín
(Google Trends, naprieč všetkými odvetviami, nefiltrované):

{titles_text}

Úloha: Vyber IBA tie témy, ktoré majú aspoň voľnú súvislosť s naším portálom.
Vylúč šport, počasie, celebrity a iné udalosti bez vzťahu k téme portálu.
Pre každú vybranú tému uveď krátku SEO frázu (2-5 slov).

Ak nič nie je relevantné, vráť prázdne pole.
Odpovedaj IBA v JSON: [{{"keyword": "..."}}]"""

    text = llm_complete(prompt, max_tokens=1000, json_output=True)
    parsed = parse_json_block(text)
    if not isinstance(parsed, list):
        return []

    candidates = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue
        candidates.append(CandidateRow(
            keyword=kw,
            keyword_normalized=slugify(kw, separator=" ", lowercase=True),
            source="trends_now",
        ))

    logger.info("Trending Now RSS filtered: %d relevant candidates for %s", len(candidates), portal_key)
    return candidates
