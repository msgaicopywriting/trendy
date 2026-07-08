"""Sitemap scraper — stiahne sitemap.xml portálu, extrahuje URL + title + H1."""
from __future__ import annotations

import re
import time
import logging
from urllib.parse import urljoin, urlparse
from typing import Generator

import requests
from bs4 import BeautifulSoup
from slugify import slugify
from sqlalchemy.orm import Session

from trendy.db import PublishedArticle, Portal

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Trendy-bot/1.0 (internal SEO tool)"}
_TIMEOUT = 15


def _fetch(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.warning("Fetch failed %s: %s", url, e)
        return None


def _iter_sitemap_urls(sitemap_url: str) -> Generator[str, None, None]:
    """Recursively iterate URLs from a sitemap (handles sitemap index)."""
    content = _fetch(sitemap_url)
    if not content:
        return

    soup = BeautifulSoup(content, "xml")

    # Sitemap index — contains <sitemap> children
    sitemaps = soup.find_all("sitemap")
    if sitemaps:
        for sm in sitemaps:
            loc = sm.find("loc")
            if loc:
                yield from _iter_sitemap_urls(loc.text.strip())
        return

    # Regular sitemap — contains <url> children
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if loc:
            yield loc.text.strip()


def _extract_meta(url: str) -> dict:
    """Fetch a URL and extract title, H1, meta description."""
    content = _fetch(url)
    if not content:
        return {}

    soup = BeautifulSoup(content, "lxml")

    title = soup.find("title")
    h1 = soup.find("h1")
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})

    return {
        "title": title.get_text(strip=True) if title else None,
        "h1": h1.get_text(strip=True) if h1 else None,
        "meta_description": meta_desc.get("content", "").strip() if meta_desc else None,
    }


def normalize_slug(text: str | None) -> str:
    """Normalize text for fuzzy matching (slug form)."""
    if not text:
        return ""
    return slugify(text, separator=" ", lowercase=True)


_DEFAULT_MAX_META_FETCHES = 50


def refresh_sitemap(
    portal: Portal, db: Session, fetch_meta: bool = True, delay: float = 0.3,
    max_meta_fetches: int = _DEFAULT_MAX_META_FETCHES,
) -> int:
    """
    Scrape sitemap for portal, upsert articles into DB.

    A large sitemap (msg-life.sk has 1400+ pages) fetching title/H1/description
    per-page one at a time can take 20-40+ minutes — long enough that Streamlit
    Cloud can recycle the session mid-run and leave a permanently "running"
    pipeline row. Only the first `max_meta_fetches` pages that don't already
    have a title get scraped per call; the rest are stored with just their URL
    (slug derived from the path) and get their metadata backfilled on
    subsequent runs. Every URL is always upserted, so coverage matching
    (get_covered_slugs) still sees the full sitemap immediately.

    Returns count of newly-inserted articles.
    """
    sitemap_url = urljoin(portal.url, "/sitemap.xml")
    logger.info("Refreshing sitemap for %s from %s", portal.key, sitemap_url)

    urls = list(_iter_sitemap_urls(sitemap_url))
    logger.info("Found %d URLs in sitemap for %s", len(urls), portal.key)

    upserted = 0
    meta_fetches_used = 0
    for url in urls:
        # Skip non-article URLs (images, feeds, etc.)
        parsed = urlparse(url)
        if any(parsed.path.endswith(ext) for ext in (".xml", ".jpg", ".png", ".pdf", ".webp")):
            continue

        existing = db.query(PublishedArticle).filter_by(portal_id=portal.id, url=url).first()

        needs_meta = fetch_meta and (existing is None or not existing.title) and meta_fetches_used < max_meta_fetches
        meta = {}
        if needs_meta:
            meta = _extract_meta(url)
            meta_fetches_used += 1
            time.sleep(delay)

        if existing:
            if meta.get("title"):
                existing.title = meta["title"]
            if meta.get("h1"):
                existing.h1 = meta["h1"]
            if meta.get("meta_description"):
                existing.meta_description = meta["meta_description"]
            if meta:
                existing.slug_normalized = normalize_slug(meta.get("title") or meta.get("h1") or parsed.path)
        else:
            slug_norm = normalize_slug(meta.get("title") or meta.get("h1") or parsed.path)
            db.add(PublishedArticle(
                portal_id=portal.id,
                url=url,
                title=meta.get("title"),
                h1=meta.get("h1"),
                meta_description=meta.get("meta_description"),
                slug_normalized=slug_norm,
            ))
            upserted += 1

    db.commit()
    logger.info(
        "Upserted %d new articles for %s (%d meta fetches used)",
        upserted, portal.key, meta_fetches_used,
    )
    return upserted


def get_covered_slugs(portal: Portal, db: Session) -> set[str]:
    """Return set of normalized slugs of all known published articles."""
    rows = db.query(PublishedArticle.slug_normalized).filter_by(portal_id=portal.id).all()
    return {r[0] for r in rows if r[0]}
