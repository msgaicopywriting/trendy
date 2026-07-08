"""Tests for the 'Ďalší krok' banner stats — the data-aware workflow guidance."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import pytest

from trendy.db import Portal, Candidate, PipelineRun
from components.next_action import _stats


@pytest.fixture
def portal_na(db_session):
    p = Portal(key="msgtester", name="msgtester.sk", url="https://msgtester.sk")
    db_session.add(p)
    db_session.flush()
    return p


def test_stats_distinguishes_verified_from_discovery(db_session, portal_na):
    db_session.add(Candidate(
        portal_id=portal_na.id, keyword="overena tema", keyword_normalized="overena tema",
        status="new", needs_volume=False, volume=500,
    ))
    db_session.add(Candidate(
        portal_id=portal_na.id, keyword="discovery tema", keyword_normalized="discovery tema",
        status="new", needs_volume=True, volume=0,
    ))
    db_session.add(Candidate(
        portal_id=portal_na.id, keyword="prijata tema", keyword_normalized="prijata tema",
        status="accepted", needs_volume=False, volume=300,
    ))
    db_session.add(PipelineRun(portal_id=portal_na.id, status="completed"))
    db_session.commit()

    s = _stats(db_session, portal_na)
    assert s["active"] == 2
    assert s["verified_active"] == 1   # only the volume-verified new topic
    assert s["needs_vol"] == 1
    assert s["accepted"] == 1
    assert s["has_run"] is True


def test_stats_all_discovery_means_zero_verified(db_session, portal_na):
    """The state that must trigger the 'nahraj exporty' branch, not 'posúď'."""
    for i in range(3):
        db_session.add(Candidate(
            portal_id=portal_na.id, keyword=f"napad {i}", keyword_normalized=f"napad {i}",
            status="new", needs_volume=True, volume=0,
        ))
    db_session.add(PipelineRun(portal_id=portal_na.id, status="completed"))
    db_session.commit()

    s = _stats(db_session, portal_na)
    assert s["active"] == 3
    assert s["verified_active"] == 0
