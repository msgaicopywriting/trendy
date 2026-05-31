"""Common interface for all trend signal sources."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CandidateRow:
    """Normalized keyword candidate produced by any source."""
    keyword: str
    keyword_normalized: str = ""
    parent_topic: str | None = None
    volume: int = 0
    kd: int | None = None
    intent: str | None = None
    source: str = "unknown"
    # Optional enrichment filled in later by scoring
    cluster: str | None = None
    extra: dict = field(default_factory=dict)  # source-specific payload

    def __post_init__(self):
        if not self.keyword_normalized:
            from slugify import slugify
            self.keyword_normalized = slugify(self.keyword, separator=" ", lowercase=True)


class Source(Protocol):
    """Every source module must expose a `fetch(portal_key) -> list[CandidateRow]`."""
    name: str

    def fetch(self, portal_key: str) -> list[CandidateRow]:
        ...
