"""Zdieľaný LLM helper — extrakcia SEO kľúčových fráz z titulkov/headlinov.

Používajú ho discovery zdroje (profesia, labor_market, ...) ktoré vracajú
titulky článkov/reportov. LLM z nich vytiahne konkrétne SEO frázy namiesto
celých viet. Bez LLM kľúča (GEMINI_API_KEY) vráti prázdny zoznam (graceful).

Pozn.: názov modulu (`_claude`) je historický — provider je teraz Gemini
(viď `trendy.llm`). Ponechaný kvôli existujúcim importom.
"""
from __future__ import annotations

import logging

from trendy.llm import llm_complete

logger = logging.getLogger(__name__)

# Tematický kontext per portál — pomáha LLM filtrovať relevantné frázy
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
    Pošle batch titulkov na LLM a vráti zoznam SEO kľúčových fráz (2–5 slov).
    Vracia [] ak nie je LLM kľúč alebo extrakcia zlyhá.
    """
    if not titles:
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

    text = llm_complete(prompt, max_tokens=800)
    if not text:
        return []
    phrases = [line.strip("-•* \t").strip() for line in text.splitlines() if line.strip()]
    return [p for p in phrases if 3 <= len(p) <= 80][:max_phrases]
