"""People Also Ask source — Google PAA + related searches cez SerpAPI.

Pre seed kľúčovky portálu vytiahne reálne otázky ("Ľudia sa tiež pýtajú")
a súvisiace vyhľadávania z Google SERP-u (geo=SK, lang=sk). Toto je najsilnejší
intent signál — sú to skutočné frázy ktoré ľudia hľadajú.

Vyžaduje SERPAPI_API_KEY. Bez kľúča vráti [] (graceful degradation).
"""
from __future__ import annotations

import logging
import os
import time

import requests

from trendy.config import PORTALS
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)

_ENDPOINT = "https://serpapi.com/search.json"
_TIMEOUT = 20
_RATE_LIMIT_S = 1.0          # medzi dotazmi, šetrí kvótu
_MAX_SEEDS = 12              # koľko seed kľúčoviek max dotázať (kvóta SerpAPI)


def fetch_paa_candidates(portal_key: str) -> list[CandidateRow]:
    """
    Pre seed kľúčovky portálu vytiahne PAA otázky + related searches.
    Vracia [] ak nie je SERPAPI_API_KEY alebo portál nemá seed kľúčovky.
    """
    api_key = os.environ.get("SERPAPI_API_KEY", "")
    if not api_key:
        logger.info("SERPAPI_API_KEY not set — skipping People Also Ask source")
        return []

    portal = PORTALS.get(portal_key)
    if not portal or not portal.seed_keywords:
        return []

    seeds = portal.seed_keywords[:_MAX_SEEDS]
    seen: set[str] = set()
    candidates: list[CandidateRow] = []

    for seed in seeds:
        try:
            questions, related = _query_serpapi(seed, api_key)
        except Exception as e:
            logger.warning("SerpAPI query failed for '%s': %s", seed, e)
            time.sleep(_RATE_LIMIT_S)
            continue

        for phrase, kind in [(q, "paa") for q in questions] + [(r, "related") for r in related]:
            norm = phrase.strip().lower()
            if not norm or norm in seen or len(phrase) < 4:
                continue
            seen.add(norm)
            candidates.append(CandidateRow(
                keyword=phrase.strip(),
                keyword_normalized="",
                source="paa",
                intent="informational" if kind == "paa" else "",
                extra={"seed": seed, "paa_kind": kind},
            ))

        time.sleep(_RATE_LIMIT_S)

    logger.info("PAA: %d candidates for %s from %d seeds", len(candidates), portal_key, len(seeds))
    return candidates


def _query_serpapi(query: str, api_key: str) -> tuple[list[str], list[str]]:
    """Vráti (related_questions, related_searches) pre dotaz."""
    params = {
        "engine": "google",
        "q": query,
        "google_domain": "google.sk",
        "gl": "sk",
        "hl": "sk",
        "api_key": api_key,
    }
    resp = requests.get(_ENDPOINT, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    questions = [
        item.get("question", "").strip()
        for item in data.get("related_questions", [])
        if item.get("question")
    ]
    related = [
        item.get("query", "").strip()
        for item in data.get("related_searches", [])
        if item.get("query")
    ]
    return questions, related
