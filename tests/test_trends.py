"""Tests for the trendspy-backed Google Trends source (Fáza 1/2 optimization)."""
from datetime import datetime, timezone

import pandas as pd

from trendy.sources import trends as trends_mod


class _FakeClient:
    def __init__(self, iot_df=None, rising_df=None):
        self._iot_df = iot_df
        self._rising_df = rising_df

    def interest_over_time(self, keywords, timeframe="today 12-m", geo=""):
        return self._iot_df

    def related_queries(self, keyword, timeframe="today 3-m", geo=""):
        return {"rising": self._rising_df}

    def trending_now_by_rss(self, geo="SK"):
        return []


def _make_weekly_df(keyword: str, values: list[int]) -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=len(values), freq="W")
    return pd.DataFrame({keyword: values}, index=idx)


def test_fetch_trend_data_uses_4_week_windows(monkeypatch):
    # 52 weekly points: first 4 weeks low (yoy baseline), 44 flat, last 4 weeks spike (mom signal)
    values = [5] * 4 + [10] * 44 + [20] * 4
    df = _make_weekly_df("python", values)
    monkeypatch.setattr(trends_mod, "_get_trends_client", lambda: _FakeClient(iot_df=df))

    result = trends_mod.fetch_trend_data(["python"])
    assert "python" in result
    assert result["python"]["mom_pct"] > 50    # last 4wk (20) vs prior 4wk (10) => +100%
    assert result["python"]["yoy_pct"] > 200   # last 4wk (20) vs first 4wk (5) => +300%


def test_fetch_trend_data_empty_df_returns_empty():
    empty = pd.DataFrame()
    result = trends_mod._fetch_batch(_FakeClient(iot_df=empty), ["python"], "today 12-m")
    assert result == {}


def test_fetch_rising_queries_parses_rows(monkeypatch):
    rising_df = pd.DataFrame([{"query": "nova fraza", "value": 250}])
    monkeypatch.setattr(trends_mod, "_get_trends_client", lambda: _FakeClient(rising_df=rising_df))

    result = trends_mod.fetch_rising_queries("python")
    assert len(result) == 1
    assert result[0].keyword == "nova fraza"
    assert result[0].source == "pytrends_rising"
    assert result[0].extra["rising_value"] == 250


def test_fetch_rising_queries_handles_missing_client(monkeypatch):
    def _raise():
        raise RuntimeError("network down")
    monkeypatch.setattr(trends_mod, "_get_trends_client", _raise)
    assert trends_mod.fetch_rising_queries("python") == []


def test_fetch_trending_now_skips_without_llm(monkeypatch):
    monkeypatch.setattr(trends_mod, "llm_available", lambda: False)
    assert trends_mod.fetch_trending_now("msgtester") == []
