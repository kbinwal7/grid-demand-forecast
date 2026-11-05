import numpy as np
import pandas as pd
import pytest

import gridcast.weather as W
from gridcast.weather import (
    WeatherConfigError, WeatherError, WeatherLeakageError,
    build_weighted_weather, engineer_weather_features, resolve_run, validate_weather_config,
)


@pytest.fixture(autouse=True)
def reset_learned_mode():
    W._LEARNED["mode"] = None
    yield
    W._LEARNED["mode"] = None


def make_cfg(run_hour="00:00", offset=1, weights=(0.5, 0.5)):
    return {"weather": {
        "model": "ecmwf_ifs", "run_hour_utc": run_hour, "run_offset_days": offset,
        "availability_lag_hours": 8, "timezone": "Asia/Kolkata",
        "request_delay_s": 0, "daily_call_budget": 100,
        "locations": {
            "a": {"latitude": 10.0, "longitude": 70.0, "weight": weights[0]},
            "b": {"latitude": 20.0, "longitude": 80.0, "weight": weights[1]},
        }}}


class FakeResp:
    def __init__(self, payload=None, status=200, text=""):
        self._p, self.status_code, self.text = payload, status, text

    def json(self):
        return self._p


class FakeSession:
    """Hourly data from 00:00 IST of the run date; temperature = latitude + 0.1 * hour."""

    def __init__(self, hours=168, reject_dates=False):
        self.hours, self.reject_dates, self.calls = hours, reject_dates, 0

    def get(self, url, params, timeout):
        self.calls += 1
        if self.reject_dates and "start_date" in params:
            return FakeResp(status=400, text='{"error":true,"reason":"bad date range"}')
        run = pd.Timestamp(params["run"]).tz_localize("UTC")
        start = run.tz_convert("Asia/Kolkata").normalize().tz_localize(None)
        times = pd.date_range(start, periods=self.hours, freq=pd.Timedelta(hours=1))
        h = np.arange(self.hours)
        lat = params["latitude"]
        return FakeResp({"utc_offset_seconds": 19800, "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "temperature_2m": (lat + 0.1 * h).tolist(),
            "relative_humidity_2m": [50.0] * self.hours,
            "apparent_temperature": (lat + 0.1 * h + 2).tolist(),
            "dew_point_2m": [15.0] * self.hours,
        }})


def test_weighted_average_and_interpolation(tmp_path):
    w = build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path, session=FakeSession())
    assert w.shape == (96, 4)
    assert str(w.index.tz) == "Asia/Kolkata"
    # target day starts at hour 24 of the fake series: weighted lat 15 + 0.1*24 = 17.4, +0.025 per 15 min
    assert np.allclose(w["weighted_temperature_2m"], 17.4 + 0.025 * np.arange(96))


def test_cache_prevents_second_request(tmp_path):
    s, cfg = FakeSession(), make_cfg()
    build_weighted_weather("2025-06-10", cfg, cache_dir=tmp_path, session=s)
    build_weighted_weather("2025-06-10", cfg, cache_dir=tmp_path, session=s)
    assert s.calls == 2                                  # 2 locations, fetched once each


def test_falls_back_when_explicit_dates_are_rejected(tmp_path):
    s = FakeSession(reject_dates=True)
    w = build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path, session=s)
    assert w.shape == (96, 4)
    assert W._LEARNED["mode"] == "default"
    assert s.calls == 3                                  # a: dates(400)+default, b: default only


def test_incomplete_coverage_is_rejected(tmp_path):
    with pytest.raises(WeatherError):
        build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path, session=FakeSession(hours=30))


def test_weights_must_sum_to_one():
    with pytest.raises(WeatherConfigError):
        validate_weather_config(make_cfg(weights=(0.5, 0.4))["weather"])


@pytest.mark.parametrize("hour,offset", [("12:00", 1), ("18:00", 1), ("00:00", 0)])
def test_unpublished_runs_are_rejected(hour, offset):
    with pytest.raises(WeatherLeakageError):
        resolve_run("2025-06-10", make_cfg(hour, offset)["weather"])


def test_safe_runs_are_accepted():
    resolve_run("2025-06-10", make_cfg("00:00", 1)["weather"])
    resolve_run("2025-06-10", make_cfg("06:00", 1)["weather"])


def _weather_frame(tz=None):
    idx = pd.date_range("2025-06-01", periods=96 * 3, freq="15min", tz=tz)
    return pd.DataFrame({
        "weighted_temperature_2m": 30.0, "weighted_apparent_temperature": 34.0,
        "weighted_relative_humidity_2m": 50.0, "weighted_dew_point_2m": 15.0}, index=idx)


def test_engineered_weather_features():
    out = engineer_weather_features(_weather_frame(), cdd_base_c=24)
    assert np.allclose(out["cdd"], 6.0)
    assert np.allclose(out["app_cdd"], 10.0)
    assert np.allclose(out["temp_day_max"], 30.0)
    assert np.allclose(out["temp_max_3d_mean"], 30.0)


def test_engineer_rejects_tz_aware_index():
    with pytest.raises(ValueError):
        engineer_weather_features(_weather_frame(tz="Asia/Kolkata"))
        
class FakeSession:
    """Mimics the real API: hourly points on the UTC grid starting at the run's init time,
    labelled in local time (so IST timestamps fall at :30). temperature = latitude + 0.1 * hours since init."""

    def __init__(self, hours=168, reject_dates=False, drop_at=None):
        self.hours, self.reject_dates, self.drop_at, self.calls = hours, reject_dates, drop_at, 0

    def get(self, url, params, timeout):
        self.calls += 1
        if self.reject_dates and "start_date" in params:
            return FakeResp(status=400, text='{"error":true,"reason":"bad date range"}')
        run = pd.Timestamp(params["run"]).tz_localize("UTC")
        start = run.tz_convert("Asia/Kolkata").tz_localize(None)          # e.g. 05:30 IST
        times = pd.date_range(start, periods=self.hours, freq=pd.Timedelta(hours=1))
        h = np.arange(self.hours)
        lat = params["latitude"]
        cols = {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "temperature_2m": (lat + 0.1 * h).tolist(),
            "relative_humidity_2m": [50.0] * self.hours,
            "apparent_temperature": (lat + 0.1 * h + 2).tolist(),
            "dew_point_2m": [15.0] * self.hours,
        }
        if self.drop_at is not None:                                       # simulate a missing hour
            cols = {k: v[: self.drop_at] + v[self.drop_at + 1:] for k, v in cols.items()}
        return FakeResp({"utc_offset_seconds": 19800, "hourly": cols})


def test_weighted_average_and_interpolation(tmp_path):
    w = build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path, session=FakeSession())
    assert w.shape == (96, 4)
    assert str(w.index.tz) == "Asia/Kolkata"
    # Series starts at 05:30 IST on 06-09, so 00:00 IST on 06-10 is 18.5 h after init:
    # weighted lat 15 + 0.1 * 18.5 = 16.85, then +0.025 per 15 minutes
    assert np.allclose(w["weighted_temperature_2m"], 16.85 + 0.025 * np.arange(96))


def test_hourly_gap_inside_the_day_is_rejected(tmp_path):
    with pytest.raises(WeatherError):
        build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path,
                               session=FakeSession(drop_at=30))            # hour index 30 is inside the day


def test_half_hour_offset_grid_is_handled(tmp_path):
    w = build_weighted_weather("2025-06-10", make_cfg(), cache_dir=tmp_path, session=FakeSession())
    assert w.index[0] == pd.Timestamp("2025-06-10 00:00", tz="Asia/Kolkata")
    assert w.index[-1] == pd.Timestamp("2025-06-10 23:45", tz="Asia/Kolkata")