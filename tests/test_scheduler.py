"""Tests for the next-recommended-run date shown in the UI."""
from datetime import date

from trendy.config import settings
from trendy.scheduler import next_scheduled_run_date


def test_next_run_later_this_month():
    # cron day defaults to 1 — use an explicit reference before/after it
    day = min(settings.scheduler_cron_day, 28)
    if day > 1:
        ref = date(2026, 7, day - 1)
        assert next_scheduled_run_date(ref) == date(2026, 7, day)


def test_next_run_rolls_to_next_month():
    day = min(settings.scheduler_cron_day, 28)
    ref = date(2026, 7, day)  # on the day itself → next month
    assert next_scheduled_run_date(ref) == date(2026, 8, day)


def test_next_run_december_rollover():
    day = min(settings.scheduler_cron_day, 28)
    ref = date(2026, 12, day)
    assert next_scheduled_run_date(ref) == date(2027, 1, day)
