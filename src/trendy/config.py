from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]

# Načítaj .env do os.environ — API kľúče (GEMINI_API_KEY, REDDIT_*, PERPLEXITY_*)
# sa čítajú cez os.environ.get(), nie cez pydantic Settings. Bez tohto by .env
# kľúče lokálne nefungovali. Na Streamlit Cloud .env neexistuje (load_dotenv no-op).
load_dotenv(ROOT / ".env")


def _bridge_streamlit_secrets() -> None:
    """Premietni Streamlit Cloud secrets do os.environ PRED vytvorením Settings().

    Streamlit drží secrets v st.secrets a do env premenných ich kopíruje až pri
    prvom prístupe k st.secrets — čo sa nemusí stihnúť pred importom tohto modulu.
    Bez tohto mostíka Settings() nevidí DATABASE_URL a appka potichu spadne späť
    na efemérny SQLite (presne to sa stalo na produkcii). Existujúce env
    premenné neprepisujeme; mimo Streamlitu je toto no-op.
    """
    try:
        import streamlit as st
        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_bridge_streamlit_secrets()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = f"sqlite:///{ROOT / 'data' / 'trendy.db'}"

    # Inbox paths
    ahrefs_inbox_dir: Path = ROOT / "data" / "ahrefs_inbox"
    gsc_inbox_dir: Path = ROOT / "data" / "gsc_inbox"
    clusters_dir: Path = ROOT / "data" / "clusters"

    # Scoring weights
    weight_volume: float = 0.25
    weight_growth: float = 0.35
    weight_gap: float = 0.25
    weight_opportunity: float = 0.15

    # Volume thresholds per portal (min searches/month)
    threshold_msg_life: int = 30
    threshold_msgtester: int = 10
    threshold_msgprogramator: int = 15

    # Lifecycle cooldowns (days)
    cooldown_rejected_days: int = 90
    cooldown_published_days: int = 365
    snooze_default_days: int = 30

    # Google Trends — 6 s medzi requestami; pri 3 s Google konzistentne vracia 429
    # (trendspy sám odporúča request_delay=6.0)
    pytrends_rate_limit_seconds: float = 6.0
    pytrends_language: str = "sk"
    pytrends_geo: str = "SK"

    # Scheduler — mesačný refresh (deň v mesiaci 1-31)
    scheduler_cron_hour: int = 6
    scheduler_cron_minute: int = 0
    scheduler_cron_day: int = 1


@dataclass
class PortalConfig:
    key: str           # internal ID: "msg-life", "msgtester", "msgprogramator"
    name: str          # display name
    url: str           # base URL (for sitemap scraping)
    volume_threshold: int
    color: str         # hex color for UI
    seed_keywords: list[str] = field(default_factory=list)
    competitor_domains: list[str] = field(default_factory=list)


def get_portal_registry(settings: Settings) -> dict[str, PortalConfig]:
    return {
        "msg-life": PortalConfig(
            key="msg-life",
            name="msg-life.sk",
            url="https://www.msg-life.sk",
            volume_threshold=settings.threshold_msg_life,
            color="#0057A8",
            seed_keywords=[
                "práca z domu", "homeoffice", "životopis", "pracovný pohovor",
                "benefity zamestnanca", "employer branding", "insurtech", "poistenie",
                "HR", "onboarding", "tímová kultúra",
            ],
            competitor_domains=[
                "profesia.sk", "kariera.sk", "platy.sk",
            ],
        ),
        "msgtester": PortalConfig(
            key="msgtester",
            name="msgtester.sk",
            url="https://msgtester.sk",
            volume_threshold=settings.threshold_msgtester,
            color="#E05206",
            seed_keywords=[
                "testovanie softvéru", "manuálne testovanie", "automatizácia testov",
                "selenium", "cypress", "QA", "bug report", "testovací prípad",
            ],
            competitor_domains=[
                "itnetwork.cz", "testovani.cz",
            ],
        ),
        "msgprogramator": PortalConfig(
            key="msgprogramator",
            name="msgprogramator.sk",
            url="https://msgprogramator.sk",
            volume_threshold=settings.threshold_msgprogramator,
            color="#2BAE66",
            seed_keywords=[
                "programovanie", "python tutoriál", "javascript", "react",
                "git", "docker", "API", "backend", "frontend", "kariéra programátor",
            ],
            competitor_domains=[
                "itnetwork.cz", "programujte.com", "zdrojak.cz",
            ],
        ),
    }


# Singleton — import this everywhere
settings = Settings()
PORTALS = get_portal_registry(settings)
