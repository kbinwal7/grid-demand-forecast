"""Leakage-safe, demand-weighted weather forecasts from Open-Meteo Single Runs.

Training and serving use the SAME rule: the forecast run issued `run_offset_days`
before the target day at `run_hour_utc`. A guard guarantees that run was published
before the day-ahead cutoff (00:00 local on the target day).

Weather data by Open-Meteo.com (CC BY 4.0, free for non-commercial use).
"""
from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
WEATHER_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "dew_point_2m",
]
PLAUSIBLE_RANGE = {                       # catches unit / scale mistakes
    "temperature_2m": (-20.0, 60.0),
    "apparent_temperature": (-30.0, 70.0),
    "dew_point_2m": (-40.0, 40.0),
    "relative_humidity_2m": (0.0, 100.0),
}
VALID_RUN_HOURS = ("00:00", "06:00", "12:00", "18:00")
WINDOW_MODES = ("auto", "dates", "default")
STEPS_PER_DAY = 96
DEFAULT_CACHE_DIR = "data/interim/weather_cache"
DEFAULT_OUT_PATH = "data/interim/weather_weighted.parquet"
ONE_HOUR = pd.Timedelta(hours=1)

# Remembers which request style worked, so a backfill doesn't pay for fallbacks every day.
_LEARNED = {"mode": None}


# ------------------------------------------------------------------ errors
class WeatherConfigError(ValueError):
    """Bad configuration. Would fail the same way every day, so never swallowed."""


class WeatherLeakageError(WeatherConfigError):
    """The requested forecast run would not have been available at forecast time."""


class WeatherError(RuntimeError):
    """Data or network problem for one location/day. Safe to skip and retry later."""


class WeatherCoverageError(WeatherError):
    """The response did not contain the full target day (or had missing values)."""


class WeatherBadRequestError(WeatherError):
    """The API rejected the request (HTTP 400 or an error payload)."""


class WeatherRateLimitError(WeatherError):
    """Hit the API's rate limit or our own call budget. Stop and resume later."""


# ------------------------------------------------------------------ HTTP
def create_session() -> requests.Session:
    retry = Retry(
        total=5, connect=5, read=5, status=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
        raise_on_status=False,            # return the last response so we can read the API's reason
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "GridDemandForecast/1.0"})
    return session


_SESSION: requests.Session | None = None


def _default_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = create_session()
    return _SESSION


class RequestThrottle:
    """Spaces requests out and enforces a per-run call budget.
    Cache hits never reach this, so they cost nothing."""

    def __init__(self, min_interval_s: float = 1.0, daily_budget: int = 8000):
        self.min_interval_s = float(min_interval_s)
        self.daily_budget = int(daily_budget)
        self.calls = 0
        self._last = 0.0

    def wait(self) -> None:
        if self.calls >= self.daily_budget:
            raise WeatherRateLimitError(
                f"Call budget of {self.daily_budget} reached. Re-run later; cached days are kept."
            )
        gap = time.monotonic() - self._last
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last = time.monotonic()
        self.calls += 1


def make_throttle(weather_cfg: dict) -> RequestThrottle:
    return RequestThrottle(
        weather_cfg.get("request_delay_s", 1.0),
        weather_cfg.get("daily_call_budget", 8000),
    )


def _request(session, params: dict, throttle, context: str) -> dict:
    if throttle is not None:
        throttle.wait()
    try:
        resp = session.get(API_URL, params=params, timeout=(15, 90))
    except requests.RequestException as exc:
        raise WeatherError(f"Request failed for {context}: {exc}") from exc

    if resp.status_code == 429:
        raise WeatherRateLimitError(f"HTTP 429 (rate limited) for {context}: {resp.text[:200]}")
    if resp.status_code == 400:
        raise WeatherBadRequestError(f"HTTP 400 for {context}: {resp.text[:300]}")
    if resp.status_code != 200:
        raise WeatherError(f"HTTP {resp.status_code} for {context}: {resp.text[:300]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise WeatherError(f"Non-JSON response for {context}") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise WeatherBadRequestError(f"API error for {context}: {payload.get('reason')}")
    return payload


# ------------------------------------------------------------------ config + guard
def validate_weather_config(weather_cfg: dict) -> dict[str, float]:
    """Validate everything up front and return {location: weight}."""
    required = ("model", "run_hour_utc", "run_offset_days", "timezone", "locations")
    missing = [k for k in required if k not in weather_cfg]
    if missing:
        raise WeatherConfigError(f"weather config is missing keys: {missing}")

    if weather_cfg["run_hour_utc"] not in VALID_RUN_HOURS:
        raise WeatherConfigError(f"run_hour_utc must be one of {VALID_RUN_HOURS}")
    if int(weather_cfg["run_offset_days"]) < 0:
        raise WeatherConfigError("run_offset_days cannot be negative")
    if weather_cfg.get("window_mode", "auto") not in WINDOW_MODES:
        raise WeatherConfigError(f"window_mode must be one of {WINDOW_MODES}")
    try:
        pd.Timestamp("2025-01-01").tz_localize(weather_cfg["timezone"])
    except Exception as exc:
        raise WeatherConfigError(f"Unknown timezone: {weather_cfg['timezone']!r}") from exc

    locations = weather_cfg["locations"]
    if not isinstance(locations, dict) or not locations:
        raise WeatherConfigError("No weather locations configured.")

    weights: dict[str, float] = {}
    for name, info in locations.items():
        if not isinstance(info, dict) or any(k not in info for k in ("latitude", "longitude", "weight")):
            raise WeatherConfigError(f"{name}: needs latitude, longitude and weight")
        lat, lon, w = float(info["latitude"]), float(info["longitude"]), float(info["weight"])
        if not all(math.isfinite(v) for v in (lat, lon, w)):
            raise WeatherConfigError(f"{name}: non-finite value")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise WeatherConfigError(f"{name}: coordinates out of range")
        if w < 0:
            raise WeatherConfigError(f"{name}: negative weight")
        weights[name] = w

    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise WeatherConfigError(f"Weights must sum to 1, got {total:.6f}")
    return weights


def resolve_run(target_date, weather_cfg: dict):
    """Return (target_start_local, run_utc, run_string) and enforce the leakage guard.

    The run date is a UTC calendar date: target date minus `run_offset_days`.
    A run is only usable if (init time + availability lag) <= the moment the
    day-ahead forecast is made (00:00 local on the target day).
    """
    tz = weather_cfg["timezone"]
    day = pd.Timestamp(target_date).normalize()
    if day.tzinfo is not None:
        day = day.tz_localize(None)

    target_start = day.tz_localize(tz)
    hh, mm = (int(x) for x in weather_cfg["run_hour_utc"].split(":"))
    run_utc = (
        day - pd.Timedelta(days=int(weather_cfg["run_offset_days"]))
        + pd.Timedelta(hours=hh, minutes=mm)
    ).tz_localize("UTC")

    available = run_utc + pd.Timedelta(hours=float(weather_cfg.get("availability_lag_hours", 8)))
    cutoff = target_start.tz_convert("UTC")
    if available > cutoff:
        raise WeatherLeakageError(
            f"Run {run_utc:%Y-%m-%d %H:%M}Z is not published until about {available:%Y-%m-%d %H:%M}Z, "
            f"after the forecast cutoff {cutoff:%Y-%m-%d %H:%M}Z. Use an earlier run "
            f"(e.g. run_hour_utc 00:00 with run_offset_days 1)."
        )
    return target_start, run_utc, run_utc.strftime("%Y-%m-%dT%H:%M")


# ------------------------------------------------------------------ paths + cache
def _resolve_path(path, cfg: dict) -> Path:
    """Relative paths are anchored at the repo root (cfg['root']) when it is known."""
    p = Path(path)
    root = cfg.get("root")
    return p if (p.is_absolute() or not root) else Path(root) / p


def weather_table_path(cfg: dict) -> Path:
    return _resolve_path(cfg["weather"].get("features_path", DEFAULT_OUT_PATH), cfg)


def _cache_path(cache_dir, location_name: str, target_date, weather_cfg: dict) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", location_name)
    day = pd.Timestamp(target_date).strftime("%Y-%m-%d")
    run_key = weather_cfg["run_hour_utc"].replace(":", "")
    return Path(cache_dir) / (
        f"{safe}_{day}_{weather_cfg['model']}_r{run_key}_d{int(weather_cfg['run_offset_days'])}.csv"
    )


def _read_cache(path: Path, expected_index: pd.DatetimeIndex) -> pd.DataFrame | None:
    """Return the cached frame only if complete and valid; otherwise None (refetch)."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        times = pd.to_datetime(df.pop("time"), utc=True).dt.tz_convert(expected_index.tz)
        df.index = pd.DatetimeIndex(times, name="time")
        df = df[WEATHER_VARIABLES].astype(float)
    except Exception:
        return None
    if len(df) == STEPS_PER_DAY and df.index.equals(expected_index) and np.isfinite(df.to_numpy()).all():
        return df
    return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")        # write-then-rename: never leaves a half-written file
    out = df.copy()
    out.index.name = "time"
    out.reset_index().to_csv(tmp, index=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ parsing
def _hourly_frame(payload: dict, target_start: pd.Timestamp, context: str) -> pd.DataFrame:
    """Parse hourly data and return the block of hourly points that brackets the target day.

    The API's hourly grid is UTC-aligned, so in local time it can sit at :30 (e.g. IST).
    We therefore don't require points exactly at 00:00; we require a point at or before
    00:00, a point at or after 23:45, and a gap-free hourly series in between.
    """
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not hourly or "time" not in hourly:
        raise WeatherError(f"No hourly data returned for {context}")
    absent = [v for v in WEATHER_VARIABLES if v not in hourly]
    if absent:
        raise WeatherError(f"{context}: response is missing variables {absent}")

    expected_offset = int(target_start.utcoffset().total_seconds())
    got = payload.get("utc_offset_seconds")
    if got is not None and int(got) != expected_offset:
        raise WeatherError(f"{context}: UTC offset {got}s does not match expected {expected_offset}s")

    try:
        index = pd.DatetimeIndex(pd.to_datetime(hourly["time"])).tz_localize(target_start.tz)
        frame = pd.DataFrame(
            {v: pd.to_numeric(pd.Series(hourly[v]), errors="coerce").to_numpy() for v in WEATHER_VARIABLES},
            index=index,
        )
    except Exception as exc:
        raise WeatherError(f"{context}: could not parse hourly data: {exc}") from exc

    frame = frame[~frame.index.duplicated(keep="first")].sort_index()

    window_start = target_start
    window_end = target_start + pd.Timedelta(days=1) - pd.Timedelta(minutes=15)   # last 15-min point

    if frame.index.min() > window_start or frame.index.max() < window_end:
        raise WeatherCoverageError(
            f"{context}: hourly data does not cover the target day. "
            f"Got {len(frame)} hours from {frame.index.min()} to {frame.index.max()}; "
            f"need {window_start} to {window_end}"
        )

    lower = frame.index[frame.index <= window_start].max()    # last point at/before 00:00
    upper = frame.index[frame.index >= window_end].min()      # first point at/after 23:45
    block = frame.loc[lower:upper]

    steps = block.index.to_series().diff().dropna()
    irregular = steps[steps != ONE_HOUR]
    if len(irregular):
        raise WeatherCoverageError(
            f"{context}: hourly series has gaps or irregular steps (first at {irregular.index[0]})"
        )
    bad = block.columns[block.isna().any()].tolist()
    if bad:
        raise WeatherCoverageError(f"{context}: missing values in {bad}")
    return block
def _to_quarter_hour(block: pd.DataFrame, quarter: pd.DatetimeIndex, context: str) -> pd.DataFrame:
    """Linear interpolation hourly -> 15 min. Safe because `block` has no NaN and
    includes both endpoints, so every interior point is fully determined."""
    grid = block.reindex(block.index.union(quarter))
    aligned = grid.interpolate(method="time").loc[quarter].astype(float)

    if aligned.shape != (STEPS_PER_DAY, len(WEATHER_VARIABLES)):
        raise WeatherError(f"{context}: unexpected shape {aligned.shape}")
    if not np.isfinite(aligned.to_numpy()).all():
        raise WeatherError(f"{context}: non-finite values after interpolation")
    for var, (lo, hi) in PLAUSIBLE_RANGE.items():
        if aligned[var].min() < lo or aligned[var].max() > hi:
            raise WeatherError(
                f"{context}: {var} outside plausible range [{lo}, {hi}] "
                f"(got {aligned[var].min():.1f} to {aligned[var].max():.1f})"
            )
    aligned.index.name = "time"
    return aligned


# ------------------------------------------------------------------ request window logic
def _mode_order(weather_cfg: dict) -> list[str]:
    configured = weather_cfg.get("window_mode", "auto")
    if configured != "auto":
        return [configured]
    first = _LEARNED["mode"] or "dates"
    return [first] + [m for m in ("dates", "default") if m != first]


def _window_params(base: dict, mode: str, target_start: pd.Timestamp) -> dict:
    params = dict(base)
    if mode == "dates":        # one day either side, so the target day is fully bracketed
        params["start_date"] = (target_start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        params["end_date"] = (target_start + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return params
def _remember_mode(mode: str, weather_cfg: dict) -> None:
    if weather_cfg.get("window_mode", "auto") == "auto" and _LEARNED["mode"] != mode:
        _LEARNED["mode"] = mode
        print(f"[weather] request window mode that works: {mode!r} "
              f"(pin it with weather.window_mode in config.yaml)")


# ------------------------------------------------------------------ public API
def fetch_location_weather(
    target_date, location_name, location_cfg, weather_cfg,
    cache_dir=DEFAULT_CACHE_DIR, session=None, throttle=None,
) -> pd.DataFrame:
    """96 quarter-hour rows (tz-aware, local time) of forecast weather for one location."""
    target_start, _, run_str = resolve_run(target_date, weather_cfg)       # leakage guard first
    quarter = pd.date_range(
        target_start, target_start + pd.Timedelta(days=1), freq="15min", inclusive="left"
    )

    path = _cache_path(cache_dir, location_name, target_date, weather_cfg)
    cached = _read_cache(path, quarter)
    if cached is not None:
        return cached

    context = f"{location_name}, target {target_start.date()}, run {run_str}"
    base = {
        "latitude": float(location_cfg["latitude"]),
        "longitude": float(location_cfg["longitude"]),
        "run": run_str,
        "models": weather_cfg["model"],
        "hourly": ",".join(WEATHER_VARIABLES),
        "timezone": weather_cfg["timezone"],
        "temperature_unit": "celsius",
    }
    session = session or _default_session()

    last_exc: Exception | None = None
    block = None
    for mode in _mode_order(weather_cfg):
        ctx = f"{context} [window={mode}]"
        try:
            payload = _request(session, _window_params(base, mode, target_start), throttle, ctx)
            block = _hourly_frame(payload, target_start, ctx)
        except (WeatherCoverageError, WeatherBadRequestError) as exc:
            last_exc = exc                 # window problem: try the other request style
            continue
        _remember_mode(mode, weather_cfg)
        break
    if block is None:
        raise WeatherError(f"No request mode returned usable data for {context}. Last error: {last_exc}") from last_exc

    aligned = _to_quarter_hour(block, quarter, context)
    _write_cache(path, aligned)
    return aligned


def build_weighted_weather(target_date, cfg, cache_dir=None, session=None, throttle=None) -> pd.DataFrame:
    """Demand-weighted national weather for one target day: 96 rows, tz-aware, 4 columns."""
    weather_cfg = cfg["weather"]
    weights = validate_weather_config(weather_cfg)
    cache_dir = cache_dir or _resolve_path(weather_cfg.get("cache_dir", DEFAULT_CACHE_DIR), cfg)

    frames = {}
    for name, loc in weather_cfg["locations"].items():
        if weights[name] == 0:
            continue                       # no weight, no API call
        frames[name] = fetch_location_weather(
            target_date, name, loc, weather_cfg, cache_dir, session, throttle
        )

    reference = next(iter(frames.values())).index
    for name, frame in frames.items():
        if not frame.index.equals(reference):
            raise WeatherError(f"Timestamp mismatch for location {name}")

    total_weight = sum(weights[n] for n in frames)
    weighted = sum(frames[n] * weights[n] for n in frames) / total_weight
    weighted.columns = [f"weighted_{c}" for c in weighted.columns]

    if len(weighted) != STEPS_PER_DAY or weighted.isna().any().any():
        raise WeatherError("Weighted weather is incomplete.")
    return weighted


def load_weather(path) -> pd.DataFrame:
    """Load the saved weather table (tz-naive local time, matching the demand index)."""
    df = pd.read_parquet(path).sort_index()
    if df.index.tz is not None:
        raise WeatherError("Saved weather index must be tz-naive local time.")
    return df


def _merge_and_save(existing, pending, out_path: Path):
    if not pending:
        return existing
    new = pd.concat(pending)
    merged = new if existing is None else pd.concat([existing, new])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    tmp = out_path.with_suffix(".tmp")
    merged.to_parquet(tmp)
    os.replace(tmp, out_path)
    return merged


def build_weather_history(
    dates, cfg, out_path=None, cache_dir=None, session=None,
    checkpoint_every: int = 50, verbose: bool = True,
):
    """Backfill weather for many days. Resumable, throttled, tolerant of single-day failures.

    Returns (weather_table, failures). Re-run to retry failed days or to continue after
    hitting the rate limit; finished days are never re-fetched.
    """
    weather_cfg = cfg["weather"]
    validate_weather_config(weather_cfg)
    out_path = Path(out_path) if out_path else weather_table_path(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    throttle = make_throttle(weather_cfg)

    days = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().unique().sort_values()
    merged = load_weather(out_path) if out_path.exists() else None
    done = set(merged.index.normalize().unique()) if merged is not None else set()
    todo = [d for d in days if d not in done]
    if verbose:
        print(f"{len(days)} days requested | {len(done & set(days))} already saved | {len(todo)} to fetch")

    pending, failures = [], []
    try:
        for i, day in enumerate(todo, 1):
            try:
                w = build_weighted_weather(day, cfg, cache_dir, session, throttle)
            except WeatherRateLimitError as exc:
                print(f"STOPPED: {exc}\nRe-run later; finished days are saved.")
                break
            except WeatherError as exc:                 # config errors are NOT caught here
                failures.append((day, str(exc)))
                continue
            pending.append(w.tz_localize(None))         # store tz-naive local time
            if len(pending) >= checkpoint_every:
                merged, pending = _merge_and_save(merged, pending, out_path), []
            if verbose and i % 25 == 0:
                print(f"  {i}/{len(todo)} days | API calls this run: {throttle.calls}")
    finally:
        merged = _merge_and_save(merged, pending, out_path)

    if merged is None:
        first = failures[0][1] if failures else "unknown"
        raise WeatherError(f"No weather data was collected. First failure: {first}")
    if verbose:
        print(f"Saved {len(merged.index.normalize().unique())} days to {out_path} "
              f"| failures: {len(failures)} | API calls: {throttle.calls}")
    return merged, failures


# ------------------------------------------------------------------ features
def engineer_weather_features(weighted: pd.DataFrame, cdd_base_c: float = 24.0) -> pd.DataFrame:
    """Derive demand-relevant features. All are known at 00:00 on the target day,
    because the whole day's forecast comes from an earlier model run."""
    if weighted.index.tz is not None:
        raise ValueError("Pass a tz-naive local-time index (as saved by build_weather_history).")
    needed = ["weighted_temperature_2m", "weighted_apparent_temperature"]
    absent = [c for c in needed if c not in weighted.columns]
    if absent:
        raise KeyError(f"weather table is missing columns: {absent}")

    out = weighted.copy()
    t = out["weighted_temperature_2m"]
    a = out["weighted_apparent_temperature"]

    out["cdd"] = (t - cdd_base_c).clip(lower=0)          # cooling degrees (air-conditioning driver)
    out["app_cdd"] = (a - cdd_base_c).clip(lower=0)

    day = out.index.normalize()
    out["temp_day_max"] = t.groupby(day).transform("max")
    out["temp_day_mean"] = t.groupby(day).transform("mean")

    # Heat-wave accumulation: mean of the last 3 daily maxima (today + 2 previous forecast days).
    # Reindex to a full daily range first so missing days don't shift the window.
    daily_max = t.groupby(day).max()
    daily_max = daily_max.reindex(pd.date_range(daily_max.index.min(), daily_max.index.max(), freq="D"))
    heat3 = daily_max.rolling(3, min_periods=1).mean()
    out["temp_max_3d_mean"] = heat3.reindex(day).to_numpy()
    return out