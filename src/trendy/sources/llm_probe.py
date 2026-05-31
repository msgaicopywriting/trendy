"""LLM probing source — Claude API self-query + optional Perplexity API pre fresh trends."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date

from slugify import slugify

from trendy.config import PORTALS
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)


def fetch_claude_probe(portal_key: str) -> list[CandidateRow]:
    """
    Ask Claude to suggest trending / emerging topics for portal_key based on
    its training knowledge. Best for newly emerging themes not yet in Ahrefs.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — skipping Claude LLM probe")
        return []

    portal = PORTALS.get(portal_key)
    if not portal:
        return []

    context = _build_portal_context(portal_key)
    today = date.today()

    prompt = f"""Si SEO expert pre portál {portal.name} ({context}).

Dnes je {today.strftime('%B %Y')}.

Úloha: Navrh 15-20 trendových tém/kľúčových fráz ktoré sú relevantné pre náš portál a ktoré:
1. Zaznamenávajú rast záujmu (nové technológie, zmeny v legislatíve, emerging best practices)
2. Ešte nie sú bežne pokryté na slovenských portáloch (content gap príležitosť)
3. Majú potenciál pre SEO traffic (ľudia ich aktívne hľadajú)

Pre každú tému uveď:
- keyword: krátka fráza 2-5 slov (slovenčina alebo angličtina podľa toho ako ľudia reálne hľadajú na SK)
- reason: prečo je táto téma trendová práve teraz
- category: emerging_tech / legislative / best_practice / career / tool / other

Odpovedaj IBA v JSON: [{{"keyword": "...", "reason": "...", "category": "..."}}]"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = msg.content[0].text.strip()
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
                source="claude_probe",
                extra={"reason": item.get("reason"), "category": item.get("category")},
            ))

        logger.info("Claude probe: %d candidates for %s", len(candidates), portal_key)
        return candidates

    except Exception as e:
        logger.error("Claude probe failed for %s: %s", portal_key, e)
        return []


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
        text = r.json()["choices"][0]["message"]["content"].strip()
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
