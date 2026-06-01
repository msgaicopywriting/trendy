"""RSS/Feedly source — parser feedov + LLM summarization tém (Gemini)."""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from slugify import slugify

from trendy.llm import llm_available, llm_complete, parse_json_block
from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Trendy-bot/1.0 (internal RSS reader)"}
_TIMEOUT = 15

# RSS feeds per portal key — curated list of relevant SK/CZ/EN feeds
PORTAL_FEEDS: dict[str, list[str]] = {
    "msg-life": [
        "https://www.etrend.sk/rss/ekonomika.xml",
        "https://www.hnonline.sk/rss/ekonomika",
        "https://finweb.hnonline.sk/rss/all",
        "https://www.shrm.org/rss/hr-news.xml",
        "https://feeds.feedburner.com/ERE-Recruiting-Intelligence",
    ],
    "msgtester": [
        "https://www.ministryoftesting.com/feed",
        "https://stickyminds.com/rss.xml",
        "https://automationintesting.com/feed.xml",
        "https://testingpodcast.com/feed/",
    ],
    "msgprogramator": [
        "https://dev.to/feed",
        "https://css-tricks.com/feed/",
        "https://hnrss.org/frontpage",
        "https://www.root.cz/rss/clanky/",
        "https://zdrojak.cz/feed/",
    ],
}

# Max age in days for RSS items
MAX_AGE_DAYS = 14


def fetch_rss_candidates(portal_key: str) -> list[CandidateRow]:
    """
    Fetch recent RSS items from portal feeds, extract titles as candidate keywords,
    optionally summarize via Claude API to extract clean topic keywords.
    """
    feeds = PORTAL_FEEDS.get(portal_key, [])
    if not feeds:
        return []

    raw_titles: list[dict] = []
    for feed_url in feeds:
        try:
            items = _parse_feed(feed_url)
            raw_titles.extend(items)
        except Exception as e:
            logger.warning("RSS feed failed %s: %s", feed_url, e)

    if not raw_titles:
        return []

    # Try LLM summarization to extract clean topic keywords
    if llm_available():
        return _summarize_via_llm(raw_titles, portal_key)
    else:
        # Fallback: use titles directly as candidates
        return [
            CandidateRow(
                keyword=item["title"],
                keyword_normalized=slugify(item["title"], separator=" ", lowercase=True),
                source="rss",
                extra={"feed_url": item["feed_url"], "item_url": item.get("url")},
            )
            for item in raw_titles
            if item.get("title")
        ]


def _parse_feed(feed_url: str) -> list[dict]:
    """Parse RSS/Atom feed, return list of {title, url, published, feed_url}."""
    try:
        r = requests.get(feed_url, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Feed fetch failed: {e}") from e

    root = ET.fromstring(r.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    items = []
    now = datetime.now(timezone.utc)

    # RSS 2.0
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        if title_el is None:
            continue

        title = (title_el.text or "").strip()
        url = (link_el.text or "").strip() if link_el is not None else ""

        # Age filter
        if pub_el is not None and pub_el.text:
            try:
                pub_dt = parsedate_to_datetime(pub_el.text.strip())
                if (now - pub_dt).days > MAX_AGE_DAYS:
                    continue
            except Exception:
                pass

        if title:
            items.append({"title": _clean_rss_title(title), "url": url, "feed_url": feed_url})

    # Atom
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        link_el = entry.find("atom:link", ns)
        pub_el = entry.find("atom:updated", ns) or entry.find("atom:published", ns)
        if title_el is None:
            continue

        title = (title_el.text or "").strip()
        url = link_el.get("href", "") if link_el is not None else ""

        if pub_el is not None and pub_el.text:
            try:
                pub_dt = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00"))
                if (now - pub_dt).days > MAX_AGE_DAYS:
                    continue
            except Exception:
                pass

        if title:
            items.append({"title": _clean_rss_title(title), "url": url, "feed_url": feed_url})

    return items


def _clean_rss_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title)  # strip HTML tags
    title = title.strip(" -–—:.,\"'")
    return title


def _summarize_via_llm(items: list[dict], portal_key: str) -> list[CandidateRow]:
    """
    Send batch of RSS titles to the LLM to extract clean searchable topics.
    The LLM filters noise, deduplicates similar themes, returns keyword-style topics.
    """
    titles_text = "\n".join(f"- {item['title']}" for item in items[:80])

    portal_context = {
        "msg-life": "HR, employer branding, kariéra, insurtech, poisťovníctvo, Slovak market",
        "msgtester": "software testing, QA, automatizácia testov, test engineering",
        "msgprogramator": "programovanie, software development, tech kariéra",
    }.get(portal_key, "tech a business")

    prompt = f"""Si SEO analytik pre portál zameraný na: {portal_context}.

Nasledujúce sú nadpisy článkov z RSS feedov za posledných 14 dní:

{titles_text}

Úloha:
1. Identifikuj z týchto nadpisov 10-20 jedinečných TEMATICKÝCH OKRUHOV relevantných pre náš portál
2. Každý okruh vyjadrí ako krátku KĽÚČOVÚ FRÁZU (2-5 slov) vhodnú pre SEO (nie celý nadpis)
3. Vylúč: brandové mená, veľmi úzke aktuálne udalosti, duplicity, irelevantné témy

Odpovedaj v JSON: [{{"keyword": "...", "source_titles": ["...", "..."]}}]
Iba JSON, žiadny iný text."""

    text = llm_complete(prompt, max_tokens=1500, json_output=True)
    parsed = parse_json_block(text)

    if isinstance(parsed, list):
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
                source="rss_llm",
                extra={"source_titles": item.get("source_titles", [])},
            ))

        logger.info("RSS LLM summary: %d topic keywords for %s", len(candidates), portal_key)
        return candidates

    # Parsing failed → fallback to raw titles
    logger.error("RSS LLM summarization returned no parseable JSON — using raw titles")
    return [
        CandidateRow(
            keyword=item["title"],
            keyword_normalized=slugify(item["title"], separator=" ", lowercase=True),
            source="rss",
            extra={"feed_url": item.get("feed_url")},
        )
        for item in items
        if item.get("title")
    ]
