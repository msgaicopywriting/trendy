"""LLM probing source — LLM self-query (Gemini) + optional Perplexity API pre fresh trends."""
from __future__ import annotations

import logging
import os
from datetime import date

from slugify import slugify

from trendy.config import PORTALS
from trendy.llm import llm_complete, parse_json_block
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)


def fetch_llm_probe(portal_key: str) -> list[CandidateRow]:
    """
    Ask the LLM to suggest trending / emerging topics for portal_key based on
    its training knowledge. Best for newly emerging themes not yet in Ahrefs.
    """
    portal = PORTALS.get(portal_key)
    if not portal:
        return []

    context = _build_portal_context(portal_key)
    today = date.today()

    prompt = f"""Si SEO expert pre portál {portal.name} ({context}).

Dnes je {today.strftime('%B %Y')}.

Vyhľadaj na webe aktuálne diskusie, správy a trendy z posledných týždňov, aby si zistil,
čo je práve teraz relevantné — nespoliehaj sa len na svoje tréningové dáta.

Úloha: Navrh 15-20 trendových tém/kľúčových fráz ktoré sú relevantné pre náš portál a ktoré:
1. Zaznamenávajú rast záujmu (nové technológie, zmeny v legislatíve, emerging best practices)
2. Ešte nie sú bežne pokryté na slovenských portáloch (content gap príležitosť)
3. Majú potenciál pre SEO traffic (ľudia ich aktívne hľadajú)

Pre každú tému uveď:
- keyword: krátka fráza 2-5 slov (slovenčina alebo angličtina podľa toho ako ľudia reálne hľadajú na SK)
- reason: prečo je táto téma trendová práve teraz
- category: emerging_tech / legislative / best_practice / career / tool / other

Odpovedaj IBA v JSON, bez markdown formátovania: [{{"keyword": "...", "reason": "...", "category": "..."}}]"""

    text = llm_complete(prompt, max_tokens=4096, grounded=True)
    if text is None:
        # Grounding zlyhal/nedostupný (napr. starší model) — fallback na plain JSON mode
        text = llm_complete(prompt, max_tokens=4096, json_output=True)
    items = parse_json_block(text)
    if isinstance(items, dict):
        items = items.get("topics") or items.get("keywords") or next(
            (v for v in items.values() if isinstance(v, list)), []
        )
    if not isinstance(items, list):
        return []

    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue
        candidates.append(CandidateRow(
            keyword=kw,
            keyword_normalized=slugify(kw, separator=" ", lowercase=True),
            source="llm_probe",
            extra={"reason": item.get("reason"), "category": item.get("category")},
        ))

    logger.info("LLM probe: %d candidates for %s", len(candidates), portal_key)
    return candidates


def fetch_perplexity_probe(portal_key: str) -> list[CandidateRow]:
    """
    Use Perplexity API (web-grounded) for fresh, real-time trending topics.
    Requires PERPLEXITY_API_KEY env var.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        logger.info("PERPLEXITY_API_KEY not set — skipping Perplexity probe")
        return []

    portal = PORTALS.get(portal_key)
    if not portal:
        return []

    context = _build_portal_context(portal_key)

    prompt = f"""What are the 10-15 most trending topics, technologies, and keywords in {context} right now in {date.today().strftime('%B %Y')}? Focus on Slovakia and Czech Republic market. Return as JSON: [{{"keyword": "...", "reason": "..."}}]. JSON only."""

    try:
        import requests
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
            },
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        items = parse_json_block(text)
        if not isinstance(items, list):
            return []

        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kw = (item.get("keyword") or "").strip()
            if not kw:
                continue
            candidates.append(CandidateRow(
                keyword=kw,
                keyword_normalized=slugify(kw, separator=" ", lowercase=True),
                source="perplexity_probe",
                extra={"reason": item.get("reason")},
            ))

        logger.info("Perplexity probe: %d candidates for %s", len(candidates), portal_key)
        return candidates

    except Exception as e:
        logger.error("Perplexity probe failed for %s: %s", portal_key, e)
        return []


def _build_portal_context(portal_key: str) -> str:
    contexts = {
        "msg-life": "HR, employer branding, kariéra, insurtech, poisťovníctvo — pre portál msg-life.sk",
        "msgtester": "software testing, QA engineering, test automation — pre portál msgtester.sk",
        "msgprogramator": "programovanie, software development, tech kariéra — pre portál msgprogramator.sk",
    }
    return contexts.get(portal_key, "tech and business content")
