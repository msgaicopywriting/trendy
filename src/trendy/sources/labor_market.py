"""Labor market source — oficiálne dáta trhu práce (ŠÚSR, ÚPSVR, ISTP).

Scrapuje news / tlačové správy / reportové stránky štátnych inštitúcií, vytiahne
headlines a cez Claude extrahuje SEO frázy (napr. "najžiadanejšie profesie 2026",
"miera nezamestnanosti", "mzdy podľa odvetví").

Aktívne len pre msg-life.sk. Bez ANTHROPIC_API_KEY vráti [] (Claude extrakcia).

POZNÁMKA: štátne inštitúcie nemajú čisté API/RSS — scrapujú sa HTML listingy.
Hĺbkové parsovanie PDF/XLSX reportov (presné čísla) je samostatný follow-up;
tu ide o tematický discovery signál, nie o presné dáta.
"""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from trendy.sources.base import CandidateRow
from trendy.sources._claude import extract_seo_phrases

logger = logging.getLogger(__name__)

# Index / news stránky štátnych inštitúcií — len pre msg-life (trh práce).
PORTAL_PAGES: dict[str, list[str]] = {
    "msg-life": [
        "https://slovak.statistics.sk/wps/portal/ext/products/informationmessages",  # ŠÚSR info správy
        "https://www.istp.sk/clanok",                                                 # ISTP články
    ],
    "msgtester": [],
    "msgprogramator": [],
}

_MIN_LEN = 15
_MAX_LEN = 160
_TIMEOUT = 20


def fetch_labor_market_candidates(portal_key: str) -> list[CandidateRow]:
    """Scrape oficiálne stránky trhu práce → headlines → Claude frázy → CandidateRow."""
    pages = PORTAL_PAGES.get(portal_key, [])
    if not pages:
        return []

    headlines: list[str] = []
    for url in pages:
        try:
            headlines.extend(_scrape_headlines(url))
        except Exception as e:
            logger.warning("Labor market scrape failed for %s: %s", url, e)

    headlines = list(dict.fromkeys(headlines))
    if not headlines:
        return []

    logger.info("Labor market: %d headlines scraped for %s", len(headlines), portal_key)
    phrases = extract_seo_phrases(headlines, portal_key)

    candidates = [
        CandidateRow(
            keyword=p,
            keyword_normalized="",
            source="labor_market",
            extra={"origin": "trh-prace-sk"},
        )
        for p in phrases
        if p
    ]
    logger.info("Labor market: %d candidate phrases for %s", len(candidates), portal_key)
    return candidates


def _scrape_headlines(url: str) -> list[str]:
    """Vytiahne headline-like text (h1-h4 + dlhšie linky) z HTML listingu."""
    resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Trendy-bot/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    texts: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        t = tag.get_text(strip=True)
        if _MIN_LEN <= len(t) <= _MAX_LEN:
            texts.append(t)
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        if _MIN_LEN <= len(t) <= _MAX_LEN and " " in t:
            texts.append(t)

    return list(dict.fromkeys(texts))
