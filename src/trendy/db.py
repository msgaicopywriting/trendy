from __future__ import annotations

from datetime import datetime, date, timezone

def _utcnow():
    return datetime.now(timezone.utc)
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Date,
    Boolean, Text, ForeignKey, UniqueConstraint, event, inspect, text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session, sessionmaker

from trendy.config import settings


class Base(DeclarativeBase):
    pass


class Portal(Base):
    __tablename__ = "portals"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)  # "msg-life" etc.
    name = Column(String(128), nullable=False)
    url = Column(String(256), nullable=False)

    articles = relationship("PublishedArticle", back_populates="portal")
    candidates = relationship("Candidate", back_populates="portal")
    runs = relationship("PipelineRun", back_populates="portal")


class PublishedArticle(Base):
    """Articles scraped from portal sitemaps."""
    __tablename__ = "published_articles"
    __table_args__ = (UniqueConstraint("portal_id", "url"),)

    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portals.id"), nullable=False)
    url = Column(String(512), nullable=False)
    title = Column(Text)
    h1 = Column(Text)
    meta_description = Column(Text)
    slug_normalized = Column(String(512))  # normalized for matching
    last_seen = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    portal = relationship("Portal", back_populates="articles")


class GscQuery(Base):
    """Queries from manually-imported GSC CSV exports."""
    __tablename__ = "gsc_queries"
    __table_args__ = (UniqueConstraint("portal_id", "query", "export_date"),)

    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portals.id"), nullable=False)
    query = Column(String(512), nullable=False)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)
    avg_position = Column(Float)
    export_date = Column(Date, nullable=False)
    imported_at = Column(DateTime, default=_utcnow)


class Candidate(Base):
    """Keyword candidates with scoring and lifecycle state."""
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("portal_id", "keyword_normalized"),)

    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portals.id"), nullable=False)

    # Keyword data
    keyword = Column(String(512), nullable=False)
    keyword_normalized = Column(String(512), nullable=False)  # lowercase, no diacritics
    parent_topic = Column(String(512))
    cluster = Column(String(256))
    volume = Column(Integer, default=0)
    kd = Column(Integer)  # keyword difficulty 0-100
    intent = Column(String(64))  # informational / transactional / navigational / commercial
    source = Column(String(128))  # ahrefs_keywords / ahrefs_competitors / ahrefs_content / pytrends
    needs_volume = Column(Boolean, default=False)  # discovery topic with unverified search volume

    # Scoring
    trend_score = Column(Float, default=0.0)
    volume_score = Column(Float, default=0.0)
    growth_score = Column(Float, default=0.0)
    gap_score = Column(Float, default=0.0)
    opportunity_score = Column(Float, default=0.0)
    tag = Column(String(32))  # rising / newly_discovered / gap / refresh

    # Trend data (from pytrends)
    trend_data_json = Column(Text)  # JSON: {date: value, ...} 12-month
    trend_yoy_pct = Column(Float)   # year-over-year growth %
    trend_mom_pct = Column(Float)   # month-over-month growth %

    # Coverage
    gsc_avg_position = Column(Float)
    gsc_impressions = Column(Integer)
    matched_article_id = Column(Integer, ForeignKey("published_articles.id"), nullable=True)

    # Lifecycle
    status = Column(String(32), nullable=False, default="new")
    # new | seen | accepted | in_progress | rejected | snoozed | published
    status_changed_at = Column(DateTime, default=_utcnow)
    snoozed_until = Column(Date, nullable=True)
    published_url = Column(String(512), nullable=True)  # set when status=published
    brief_url = Column(String(512), nullable=True)       # set when status=in_progress
    is_returned_from_cooldown = Column(Boolean, default=False)

    # Meta
    first_seen = Column(DateTime, default=_utcnow)
    last_scored = Column(DateTime, default=_utcnow)
    ahrefs_import_file = Column(String(256))

    portal = relationship("Portal", back_populates="candidates")
    status_history = relationship("CandidateStatusHistory", back_populates="candidate")
    matched_article = relationship("PublishedArticle")


class CandidateStatusHistory(Base):
    """Audit log for all status changes."""
    __tablename__ = "candidate_status_history"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    from_status = Column(String(32))
    to_status = Column(String(32), nullable=False)
    changed_at = Column(DateTime, default=_utcnow)
    reason = Column(String(512))  # rejection reason / snooze reason / note

    candidate = relationship("Candidate", back_populates="status_history")


class PipelineRun(Base):
    """Log of pipeline executions per portal."""
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portals.id"), nullable=False)
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(32), default="running")  # running | completed | failed
    candidates_found = Column(Integer, default=0)
    candidates_suppressed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    portal = relationship("Portal", back_populates="runs")


class Seed(Base):
    """Seed keywords per portal — basis for Google Trends rising queries and
    the Ahrefs Keywords Explorer template. Auto-derived from portal data
    (GSC queries, published articles, accepted candidates) with room for
    manually-added seeds that survive every auto-refresh."""
    __tablename__ = "seeds"
    __table_args__ = (UniqueConstraint("portal_id", "keyword_normalized"),)

    id = Column(Integer, primary_key=True)
    portal_id = Column(Integer, ForeignKey("portals.id"), nullable=False)
    keyword = Column(String(256), nullable=False)
    keyword_normalized = Column(String(256), nullable=False)
    origin = Column(String(16), nullable=False)  # auto | manual | bootstrap
    active = Column(Boolean, default=True)
    source_evidence = Column(Text)  # JSON: what the seed was derived from
    created_at = Column(DateTime, default=_utcnow)

    portal = relationship("Portal")


class SuppressedCandidate(Base):
    """Candidates suppressed by cooldown in a specific run (for the suppressed panel)."""
    __tablename__ = "suppressed_candidates"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    suppressed_reason = Column(String(128))  # cooldown_rejected | cooldown_published | snoozed | accepted | in_progress


# Engine + session factory
def get_engine(db_url: str | None = None):
    url = db_url or settings.database_url
    engine = create_engine(url, connect_args={"check_same_thread": False} if "sqlite" in url else {})
    # Enable WAL mode for SQLite (better concurrent reads from Streamlit)
    if "sqlite" in url:
        @event.listens_for(engine, "connect")
        def set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
    return engine


def init_db(engine=None) -> None:
    """Create all tables. Safe to call multiple times."""
    e = engine or get_engine()
    Base.metadata.create_all(e)
    _run_migrations(e)


def _run_migrations(engine) -> None:
    """Idempotent lightweight migrations for columns added after initial release."""
    insp = inspect(engine)
    if "candidates" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("candidates")}
    if "needs_volume" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE candidates ADD COLUMN needs_volume BOOLEAN DEFAULT 0"
            ))


def get_session_factory(engine=None):
    e = engine or get_engine()
    return sessionmaker(bind=e, autoflush=False, autocommit=False)


# Convenience: module-level defaults (lazy init)
_engine = None
_SessionLocal = None


def get_db() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = get_engine()
        init_db(_engine)
        _SessionLocal = get_session_factory(_engine)
    return _SessionLocal()
