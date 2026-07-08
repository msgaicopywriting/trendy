"""APScheduler — mesačný refresh pipeline pre všetky portály."""
from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from trendy.config import settings, PORTALS

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def next_scheduled_run_date(today: date | None = None) -> date:
    """Dátum najbližšieho odporúčaného mesačného behu podľa nastavenia
    plánovača (Nastavenia → Scheduler). Slúži na zobrazenie v UI — samotný
    APScheduler beh závisí od toho, či je appka práve hore."""
    today = today or date.today()
    day = min(settings.scheduler_cron_day, 28)  # 28 = bezpečné aj pre február
    if today.day < day:
        return date(today.year, today.month, day)
    if today.month == 12:
        return date(today.year + 1, 1, day)
    return date(today.year, today.month + 1, day)


def _run_all_portals():
    from trendy.pipeline import run_pipeline
    from trendy.db import get_db
    db = get_db()
    try:
        for portal_key in PORTALS:
            try:
                run_pipeline(portal_key, db=db)
            except Exception as e:
                logger.error("Pipeline failed for %s: %s", portal_key, e)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    """Start background scheduler (call once on app startup)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_all_portals,
        CronTrigger(
            day=settings.scheduler_cron_day,
            hour=settings.scheduler_cron_hour,
            minute=settings.scheduler_cron_minute,
        ),
        id="monthly_pipeline",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — monthly pipeline runs on day %d at %02d:%02d",
        settings.scheduler_cron_day,
        settings.scheduler_cron_hour,
        settings.scheduler_cron_minute,
    )
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
