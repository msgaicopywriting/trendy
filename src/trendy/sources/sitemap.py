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


def refresh_sitemap(portal: Portal, db: Session, fetch_meta: bool = True, delay: float = 0.3) -> int:
    """
    Scrape sitemap for portal, upsert articles into DB.
    Returns count of articles upserted.
    """
    sitemap_url = urljoin(portal.url, "/sitemap.xml")
    logger.info("Refreshing sitemap for %s from %s", portal.key, sitemap_url)

    urls = list(_iter_sitemap_urls(sitemap_url))
    logger.info("Found %d URLs in sitemap for %s", len(urls), portal.key)

    upserted = 0
    for url in urls:
        # Skip non-article URLs (images, feeds, etc.)
        parsed = urlparse(url)
        if any(parsed.path.endswith(ext) for ext in (".xml", ".jpg", ".png", ".pdf", ".webp")):
            continue

        meta = {}
        if fetch_meta:
            meta = _extract_meta(url)
            time.sleep(delay)

        slug_norm = normalize_slug(meta.get("title") or meta.get("h1") or parsed.path)

        existing = db.query(PublishedArticle).filter_by(portal_id=portal.id, url=url).first()
        if existing:
            if meta.get("title"):
                existing.title = meta["title"]
            if meta.get("h1"):
                existing.h1 = meta["h1"]
            if meta.get("meta_description"):
                existing.meta_description = meta["meta_description"]
            existing.slug_normalized = slug_norm
        else:
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
    logger.info("Upserted %d new articles for %s", upserted, portal.key)
    return upserted


def get_covered_slugs(portal: Portal, db: Session) -> set[str]:
    """Return set of normalized slugs of all known published articles."""
    rows = db.query(PublishedArticle.slug_normalized).filter_by(portal_id=portal.id).all()
    return {r[0] for r in rows if r[0]}
