"""Zdieľaný Claude helper — extrakcia SEO kľúčových fráz z titulkov/headlinov.

Používajú ho discovery zdroje (profesia, labor_market, ...) ktoré vracajú
titulky článkov/reportov. Claude z nich vytiahne konkrétne SEO frázy namiesto
celých viet. Bez ANTHROPIC_API_KEY vráti prázdny zoznam (graceful degradation).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Tematický kontext per portál — pomáha Claude filtrovať relevantné frázy
PORTAL_CONTEXT: dict[str, str] = {
    "msg-life": (
        "HR, nábor, kariéra, employer branding, benefity zamestnancov, "
        "mzdy a platy, trh práce, vzdelávanie zamestnancov, poistenie, insurtech"
    ),
    "msgtester": (
        "QA, testovanie softvéru, test automatizácia, Selenium, Cypress, "
        "kvalita softvéru, AI v testovaní"
    ),
    "msgprogramator": (
        "programovanie, vývoj softvéru, webový vývoj, DevOps, cloud, "
        "AI nástroje pre developerov, kariéra v IT"
    ),
}

_MAX_TITLES = 80


def extract_seo_phrases(titles: list[str], portal_key: str, max_phrases: int = 30) -> list[str]:
    """
    Pošle batch titulkov na Claude a vráti zoznam SEO kľúčových fráz (2–5 slov).
    Vracia [] ak nie je API kľúč alebo extrakcia zlyhá.
    """
    if not titles:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — skipping Claude phrase extraction")
        return []

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic not installed — run: uv add anthropic")
        return []

    context = PORTAL_CONTEXT.get(portal_key, "IT a technológie")
    batch = titles[:_MAX_TITLES]
    titles_text = "\n".join(f"- {t}" for t in batch)

    prompt = f"""Si SEO špecialista pre slovenský portál zameraný na: {context}.

Nižšie sú titulky článkov / reportov z relevantných zdrojov. Extrahuj z nich
konkrétne SEO kľúčové frázy (2–5 slov), ktoré:
- reprezentujú tému, nie celú vetu
- sú relevantné pre obsah portálu
- majú potenciál ako téma článku na slovenskom trhu

Pravidlá:
- vráť IBA frázy, každú na novom riadku, bez číslovania a úvodov
- slovensky, 2–5 slov (napr. "najžiadanejšie profesie 2026", "mzdy v IT")
- ak titulok nemá relevantnú frázu, preskočí ho
- max {max_phrases} fráz

Titulky:
{titles_text}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        phrases = [line.strip("-•* \t").strip() for line in text.splitlines() if line.strip()]
        return [p for p in phrases if 3 <= len(p) <= 80][:max_phrases]
    except Exception as e:
        logger.warning("Claude phrase extraction failed: %s", e)
        return []
