"""TrendScore výpočet + tag klasifikácia pre každého kandidáta."""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass

from trendy.config import settings, PortalConfig

logger = logging.getLogger(__name__)

# Volume reference for log-scale normalization (max expected SK volume)
_VOLUME_REFERENCE = 10_000


@dataclass
class ScoringInput:
    volume: int = 0
    kd: int | None = None
    intent: str | None = None
    gsc_avg_position: float | None = None   # None = not ranking
    gsc_impressions: int = 0
    trend_mom_pct: float = 0.0              # month-over-month % from pytrends
    trend_yoy_pct: float = 0.0             # year-over-year %
    has_published_article: bool = False     # True = article already exists
    days_since_first_seen: int = 0         # 0 = newly discovered this run
    portal_cfg: PortalConfig | None = None
    source: str = "unknown"


@dataclass
class ScoringResult:
    trend_score: float          # 0-100 composite
    volume_score: float
    growth_score: float
    gap_score: float
    opportunity_score: float
    tag: str                    # rising | newly_discovered | gap | refresh | evergreen


def compute_score(inp: ScoringInput) -> ScoringResult:
    """Compute TrendScore and assign tag for a single candidate."""
    w1 = settings.weight_volume
    w2 = settings.weight_growth
    w3 = settings.weight_gap
    w4 = settings.weight_opportunity

    volume_score = _volume_score(inp.volume)
    growth_score = _growth_score(inp.trend_mom_pct, inp.trend_yoy_pct)
    gap_score = _gap_score(inp.gsc_avg_position, inp.has_published_article)
    opportunity_score = _opportunity_score(inp.kd, inp.intent, inp.portal_cfg)

    trend_score = (
        w1 * volume_score
        + w2 * growth_score
        + w3 * gap_score
        + w4 * opportunity_score
    )
    trend_score = round(min(100.0, max(0.0, trend_score)), 1)

    tag = _classify(volume_score, growth_score, gap_score, inp)

    return ScoringResult(
        trend_score=trend_score,
        volume_score=round(volume_score, 1),
        growth_score=round(growth_score, 1),
        gap_score=round(gap_score, 1),
        opportunity_score=round(opportunity_score, 1),
        tag=tag,
    )


def _volume_score(volume: int) -> float:
    """Log-scale normalization: 0 vol → 0, 10K+ vol → 100."""
    if volume <= 0:
        return 0.0
    return min(100.0, (math.log10(volume + 1) / math.log10(_VOLUME_REFERENCE + 1)) * 100)


def _growth_score(mom_pct: float, yoy_pct: float) -> float:
    """
    Growth score based on month-over-month and year-over-year trend.
    >50% M/M growth → 100. Negative growth → 0.
    Weighted 60% MoM (more recent) + 40% YoY (more stable).
    """
    mom_capped = max(0.0, min(mom_pct, 100.0))
    yoy_capped = max(0.0, min(yoy_pct, 100.0))
    return mom_capped * 0.6 + yoy_capped * 0.4


def _gap_score(gsc_avg_position: float | None, has_published_article: bool) -> float:
    """
    Gap = opportunity to rank where we currently don't.
    No article + not ranking → 100 (full gap)
    Ranking 1-10 → 0 (already covered)
    Ranking 11-20 → 50 (refresh candidate)
    Ranking 21-50 → 75 (weak coverage)
    Not ranking (>50 or None) → 100 if no article, 85 if article exists
    """
    if gsc_avg_position is not None:
        pos = gsc_avg_position
        if pos <= 10:
            return 0.0
        if pos <= 20:
            return 50.0
        if pos <= 50:
            return 75.0

    # Not ranking
    return 85.0 if has_published_article else 100.0


def _opportunity_score(kd: int | None, intent: str | None, portal_cfg: PortalConfig | None) -> float:
    """
    Opportunity = ease × intent fit.
    Low KD = easier to rank. Informational intent fits content portals best.
    """
    # KD component (0-100 → ease score 0-100 inverted)
    kd_score = 100.0 - float(kd) if kd is not None else 50.0

    # Intent fit (informational best for content portals)
    intent_score = _intent_fit(intent)

    return kd_score * 0.5 + intent_score * 0.5


def _intent_fit(intent: str | None) -> float:
    if not intent:
        return 50.0
    intent_lower = intent.lower()
    # All three portals are content portals — informational is best fit
    mapping = {
        "informational": 100.0,
        "informational, commercial": 80.0,
        "commercial": 60.0,
        "transactional": 30.0,
        "navigational": 10.0,
    }
    for key, score in mapping.items():
        if key in intent_lower:
            return score
    return 50.0


def _classify(volume_score: float, growth_score: float, gap_score: float, inp: ScoringInput) -> str:
    """
    Assign exactly one tag to the candidate (priority order).
    """
    # Newly discovered: seen for the first time (this run or last 30 days)
    if inp.days_since_first_seen < 30 and inp.source not in ("ahrefs_keywords",):
        if growth_score > 20 or inp.source in ("pytrends_rising", "rss_claude", "claude_probe", "perplexity_probe"):
            return "newly_discovered"

    # Rising: strong recent growth signal
    if growth_score > 60 and volume_score > 20:
        return "rising"

    # Refresh: we have coverage but ranking weak AND there's growth
    if inp.has_published_article and inp.gsc_avg_position and 11 <= inp.gsc_avg_position <= 30 and growth_score > 30:
        return "refresh"

    # Gap: no coverage, decent volume
    if gap_score >= 100 and volume_score > 15:
        return "gap"

    # Evergreen: stable, high volume, existing but not ranking
    if volume_score > 40 and growth_score < 20 and gap_score >= 75:
        return "gap"  # treat evergreen gaps as gap

    return "gap"  # default fallback
