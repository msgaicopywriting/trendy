"""Reddit source — PRAW API pre trending diskusie v relevantných subredditoch.

Aktívny len pre msgtester.sk a msgprogramator.sk — SK HR komunita na Reddite
je príliš malá na zmysluplný signál pre msg-life.sk.

Raw post titles sa pred zaradením do pipeline extrahujú cez Claude (rovnaký
vzor ako RSS) — inak by sa do candidatov dostávali vety namiesto kľúčových fráz.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from slugify import slugify

from trendy.sources.base import CandidateRow

logger = logging.getLogger(__name__)


# Subreddits per portal key — pre msg-life je Reddit vypnutý (nulová SK HR komunita)
PORTAL_SUBREDDITS: dict[str, list[str]] = {
    "msg-life": [],           # vypnuté — HR/insurtech SK komunita na Reddite neexistuje
    "msgtester": [
        "QualityAssurance",
        "softwaretesting",
        "selenium",
        "programming",
        "learnprogramming",
        "devops",
        # AI a test automatizácia
        "artificial",
        "AIAssistants",
        "ChatGPT",
        "LocalLLaMA",
        "MachineLearning",
    ],
    "msgprogramator": [
        "programming",
        "learnprogramming",
        "Python",
        "javascript",
        "webdev",
        "devops",
        "cscareerquestions",
        # AI vývoj a nástroje
        "artificial",
        "LocalLLaMA",
        "MachineLearning",
        "ChatGPT",
        "github_copilot",
        "cursor",
    ],
}

# Minimum upvotes to consider a post as a "signal"
MIN_SCORE = 10

# Max titles to send to Claude for phrase extraction in one batch
_CLAUDE_BATCH = 60


def fetch_trending(portal_key: str, limit_per_sub: int = 25) -> list[CandidateRow]:
    """
    Fetch trending posts from relevant subreddits for portal_key.
    Requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT env vars.
    Returns empty list gracefully if credentials not set, API fails, or portal disabled.
    """
    subreddits = PORTAL_SUBREDDITS.get(portal_key, [])
    if not subreddits:
        logger.debug("Reddit disabled for portal %s", portal_key)
        return []

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "Trendy-bot/1.0")

    if not client_id or not client_secret:
        logger.info("Reddit credentials not configured (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET)")
        return []

    try:
        import praw
    except ImportError:
        logger.warning("praw not installed — run: uv add praw")
        return []

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            read_only=True,
        )
    except Exception as e:
        logger.error("Reddit auth failed: %s", e)
        return []

    # --- Gather raw posts ---
    raw_posts: list[dict] = []
    for subreddit_name in subreddits:
        try:
            sub = reddit.subreddit(subreddit_name)
            for post in sub.hot(limit=limit_per_sub):
                if post.score < MIN_SCORE:
                    continue
                title = _clean_title(post.title)
                if not title or len(title) < 4:
                    continue
                raw_posts.append({
                    "title": title,
                    "subreddit": subreddit_name,
                    "url": f"https://reddit.com{post.permalink}",
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "created_utc": datetime.fromtimestamp(
                        post.created_utc, tz=timezone.utc
                    ).isoformat(),
                })
        except Exception as e:
            logger.warning("Failed to fetch r/%s: %s", subreddit_name, e)

    if not raw_posts:
        return []

    logger.info(
        "Reddit: %d raw posts for %s from %d subreddits — extracting keyword phrases via Claude",
        len(raw_posts), portal_key, len(subreddits),
    )

    # --- Extract SEO keyword phrases via Claude ---
    titles = [p["title"] for p in raw_posts]
    extracted_phrases = _extract_phrases_via_claude(titles, portal_key)

    # Build CandidateRow per extracted phrase (retain subreddit meta from best-matching post)
    candidates: list[CandidateRow] = []
    post_by_title = {p["title"]: p for p in raw_posts}

    for phrase in extracted_phrases:
        if not phrase or len(phrase) < 3:
            continue
        # Best-effort: find the closest raw post for metadata
        meta = _find_best_post(phrase, raw_posts) or raw_posts[0]
        candidates.append(CandidateRow(
            keyword=phrase,
            keyword_normalized=slugify(phrase, separator=" ", lowercase=True),
            source="reddit",
            extra={
                "subreddit": meta["subreddit"],
                "reddit_url": meta["url"],
                "score": meta["score"],
                "num_comments": meta["num_comments"],
            },
        ))

    logger.info("Reddit: %d keyword phrases extracted for %s", len(candidates), portal_key)
    return candidates


def _extract_phrases_via_claude(titles: list[str], portal_key: str) -> list[str]:
    """
    Pošle batch titulkov na Claude a vyžiada extrakciu SEO keyword fráz.
    Rovnaký prístup ako rss._summarize_via_claude — vracia len frázy, nie vety.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — Reddit phrases returned as raw titles")
        return _fallback_clean(titles)

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic not installed")
        return _fallback_clean(titles)

    portal_context = {
        "msgtester": "QA testovanie softvéru, test automatizácia, Selenium, Cypress, AI v testovaní",
        "msgprogramator": "programovanie, webový vývoj, AI nástroje pre developerov, Python, JavaScript, kariéra v IT",
    }.get(portal_key, "IT a technológie")

    batch = titles[:_CLAUDE_BATCH]
    titles_text = "\n".join(f"- {t}" for t in batch)

    prompt = f"""Si SEO špecialista pre slovenský IT portál zameraný na: {portal_context}.

Nižšie sú titulky Reddit postov z relevantných technologických subredditov.
Tvojou úlohou je extrahovať z nich konkrétne SEO kľúčové frázy (2–5 slov), ktoré:
- Reprezentujú tému alebo technológiu, nie celú vetu
- Sú relevantné pre obsah portálu
- Majú potenciál ako téma článku

Pravidlá:
- Vráť IBA frázy, každú na novom riadku, bez číslovania ani úvodov
- Nie vety, len kľúčové frázy (napr. "AI test automatizácia", "GitHub Copilot alternatívy")
- Ak titulok nemá relevantnú frázu, preskočí ho
- Max 30 fráz celkovo

Reddit titulky:
{titles_text}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        phrases = [line.strip("- •").strip() for line in text.splitlines() if line.strip()]
        return [p for p in phrases if 3 <= len(p) <= 80]
    except Exception as e:
        logger.warning("Claude phrase extraction failed: %s — using raw titles", e)
        return _fallback_clean(titles)


def _fallback_clean(titles: list[str]) -> list[str]:
    """Fallback keď Claude nie je dostupný — vráti vyčistené tituly (nie ideálne)."""
    return [_clean_title(t) for t in titles if _clean_title(t)]


def _find_best_post(phrase: str, posts: list[dict]) -> dict | None:
    """Nájde post ktorého title najlepšie sedí k extrahovane fráze (jednoduché word overlap)."""
    phrase_words = set(phrase.lower().split())
    best, best_score = None, 0
    for p in posts:
        overlap = len(phrase_words & set(p["title"].lower().split()))
        if overlap > best_score:
            best, best_score = p, overlap
    return best


def _clean_title(title: str) -> str:
    """Strip common Reddit noise from titles."""
    title = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    title = re.sub(r"\?+$", "", title)
    title = title.strip(" -–—:.,")
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0]
    return title.strip()
