"""Catch-up after downtime: missed days are found, forecast in order, and never block each other."""
import pandas as pd
import pytest

from gridcast import jobs

NOW = pd.Timestamp("2026-11-13 09:00")  # tomorrow = 2026-11-14
TOMORROW = pd.Timestamp("2026-11-14")


def days(*ds):
    return {pd.Timestamp(d) for d in ds}


def patch_store(monkeypatch, first, done):
    monkeypatch.setattr(jobs, "first_live_forecast_day", lambda engine: first)
    monkeypatch.setattr(jobs, "live_forecast_days_complete", lambda engine, s, e: done)


def test_no_live_history_only_forecasts_tomorrow(monkeypatch):
    patch_store(monkeypatch, None, set())
    assert jobs.missing_forecast_days(object(), {}, NOW) == [TOMORROW]


def test_nothing_missing_when_everything_is_covered(monkeypatch):
    done = days("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14")
    patch_store(monkeypatch, pd.Timestamp("2026-11-10"), done)
    assert jobs.missing_forecast_days(object(), {}, NOW) == []


def test_vm_down_for_five_days_returns_every_missed_day_plus_tomorrow(monkeypatch):
    # last forecast was for 2026-11-08; VM was off until now (2026-11-13)
    patch_store(monkeypatch, pd.Timestamp("2026-10-01"), days("2026-11-07", "2026-11-08"))
    out = jobs.missing_forecast_days(object(), {"jobs": {"max_catchup_days": 6}}, NOW)
    assert out == list(pd.date_range("2026-11-09", "2026-11-14"))


def test_holes_in_the_middle_are_found(monkeypatch):
    done = days("2026-11-10", "2026-11-12", "2026-11-14")
    patch_store(monkeypatch, pd.Timestamp("2026-11-10"), done)
    out = jobs.missing_forecast_days(object(), {}, NOW)
    assert out == [pd.Timestamp("2026-11-11"), pd.Timestamp("2026-11-13")]


def test_lookback_is_capped(monkeypatch):
    patch_store(monkeypatch, pd.Timestamp("2025-01-01"), set())
    out = jobs.missing_forecast_days(object(), {"jobs": {"max_catchup_days": 3}}, NOW)
    assert out == list(pd.date_range("2026-11-11", "2026-11-14"))


def test_forecast_days_runs_in_order_allows_past_and_collects_failures(monkeypatch):
    calls = []

    def fake_forecast_day(engine, cfg, day, now=None, allow_past=False, **_):
        calls.append((day, allow_past))
        if day == pd.Timestamp("2026-11-12"):
            raise jobs.DataValidationError("D-3 demand missing")
        return pd.Series([1.0])

    monkeypatch.setattr(jobs, "forecast_day", fake_forecast_day)

    with pytest.raises(RuntimeError, match="2026-11-12"):
        jobs._forecast_days(
            object(), {}, [TOMORROW, pd.Timestamp("2026-11-12"), pd.Timestamp("2026-11-11")], NOW
        )

    # chronological; the failure did not stop tomorrow's forecast; only past days may have actuals
    assert calls == [
        (pd.Timestamp("2026-11-11"), True),
        (pd.Timestamp("2026-11-12"), True),
        (TOMORROW, False),
    ]


def test_run_daily_always_includes_tomorrow_and_syncs_weather(monkeypatch):
    seen = {}
    monkeypatch.setattr(jobs, "now_local", lambda cfg: NOW)
    monkeypatch.setattr(jobs, "missing_forecast_days", lambda e, c, n: [pd.Timestamp("2026-11-12")])
    monkeypatch.setattr(jobs, "ingest", lambda e, c, now=None: seen.setdefault("ingest", True))
    monkeypatch.setattr(jobs, "_sync_weather_for", lambda e, c, d, n: seen.setdefault("weather", list(d)))
    monkeypatch.setattr(jobs, "_forecast_days", lambda e, c, d, n: seen.setdefault("forecast", list(d)) or {})

    jobs.run_daily(object(), {})

    assert seen["ingest"] is True
    assert seen["weather"] == seen["forecast"] == [pd.Timestamp("2026-11-12"), TOMORROW]


def test_run_catchup_is_a_noop_when_nothing_is_missing(monkeypatch):
    monkeypatch.setattr(jobs, "now_local", lambda cfg: NOW)
    monkeypatch.setattr(jobs, "missing_forecast_days", lambda e, c, n: [])

    def boom(*a, **k):
        raise AssertionError("must not scrape or call APIs when nothing is missing")

    monkeypatch.setattr(jobs, "ingest", boom)
    monkeypatch.setattr(jobs, "_sync_weather_for", boom)
    assert jobs.run_catchup(object(), {}) == {}


def test_sync_weather_skips_catchup_days_that_already_have_weather(monkeypatch):
    got = {}
    monkeypatch.setattr(jobs, "weather_complete_days", lambda e, d: {pd.Timestamp("2026-11-12")})
    monkeypatch.setattr(jobs, "sync_weather", lambda e, c, extra_days=(): got.setdefault("days", list(extra_days)))

    jobs._sync_weather_for(
        object(), {}, [pd.Timestamp("2026-11-11"), pd.Timestamp("2026-11-12"), TOMORROW], NOW
    )
    # 11-12 already has weather -> skipped; tomorrow is always refreshed
    assert got["days"] == [pd.Timestamp("2026-11-11"), TOMORROW]


class _FakeConn:
    def __init__(self, last):
        self.last = last

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        last = self.last

        class R:
            def scalar(self_inner):
                return last

        return R()


class _FakeEngine:
    def __init__(self, last):
        self.last = last

    def connect(self):
        return _FakeConn(self.last)


@pytest.mark.parametrize(
    "last_demand, expected",
    [
        ("2026-11-12", 8),    # healthy: default window
        ("2026-11-05", 10),   # 8-day gap -> gap + 2
        ("2026-08-01", 45),   # huge gap -> capped
        (None, 45),           # empty table -> cap
    ],
)
def test_ingest_lookback_widens_after_downtime(last_demand, expected):
    last = pd.Timestamp(last_demand).to_pydatetime() if last_demand else None
    assert jobs.ingest_lookback_days(_FakeEngine(last), {}, NOW) == expected
