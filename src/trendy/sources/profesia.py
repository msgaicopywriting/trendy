"""Profesia.sk source — najautoritatívnejší SK kariérny/HR zdroj.

Scrapuje Profesia magazín (kariérne články) + reporty trhu práce (Index trhu
práce, platové reporty). Z headlineov cez Claude extrahuje SEO frázy.

Aktívne len pre msg-life.sk. Bez ANTHROPIC_API_KEY vráti [] (graceful).
"""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from trendy.sources.base import CandidateRow
from trendy.sources._claude import extract_seo_phrases

logger = logging.getLogger(__name__)

# Index stránky ktoré obsahujú zoznamy článkov/reportov — scrapuje sa headline text.
# Len pre msg-life (kariéra/HR). Ostatné portály majú prázdny zoznam.
PORTAL_PAGES: dict[str, list[str]] = {
    "msg-life": [
        "https://www.profesia.sk/kariera/",          # kariérny magazín
        "https://www.profesia.sk/praca/clanky/",     # články o práci
    ],
    "msgtester": [],
    "msgprogramator": [],
}

# Minimálna a maximálna dĺžka headline textu (filter na navigačné linky / šum)
_MIN_LEN = 15
_MAX_LEN = 140
_TIMEOUT = 15


def fetch_profesia_candidates(portal_key: str) -> list[CandidateRow]:
    """Scrape Profesia index pages → headlines → Claude SEO frázy → CandidateRow."""
    pages = PORTAL_PAGES.get(portal_key, [])
    if not pages:
        return []

    headlines: list[str] = []
    for url in pages:
        try:
            headlines.extend(_scrape_headlines(url))
        except Exception as e:
            logger.warning("Profesia scrape failed for %s: %s", url, e)

    # Dedup
    headlines = list(dict.fromkeys(headlines))
    if not headlines:
        return []

    logger.info("Profesia: %d headlines scraped for %s — extracting phrases", len(headlines), portal_key)
    phrases = extract_seo_phrases(headlines, portal_key)

    candidates = [
        CandidateRow(
            keyword=p,
            keyword_normalized="",  # auto-fill v __post_init__
            source="profesia",
            extra={"origin": "profesia.sk"},
        )
        for p in phrases
        if p
    ]
    logger.info("Profesia: %d candidate phrases for %s", len(candidates), portal_key)
    return candidates


def _scrape_headlines(url: str) -> list[str]:
    """Vytiahne headline-like text z článkového listingu (h2/h3 + článkové linky)."""
    resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Trendy-bot/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    texts: list[str] = []
    # Headingy
    for tag in soup.find_all(["h2", "h3"]):
        t = tag.get_text(strip=True)
        if _MIN_LEN <= len(t) <= _MAX_LEN:
            texts.append(t)
    # Linky vyzerajúce ako články (dostatočne dlhý anchor text)
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        if _MIN_LEN <= len(t) <= _MAX_LEN and " " in t:
            texts.append(t)

    return list(dict.fromkeys(texts))
