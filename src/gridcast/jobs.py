"""Scheduled jobs. Each function is idempotent and fails loudly on bad data."""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from gridcast.backtest import run_backtest
from gridcast.data import load_raw, make_regular
from gridcast.db import upsert_raw                       # your existing demand upsert
from gridcast.features import build_features
from gridcast.metrics import mape
from gridcast.models import Blend, LightGBMModel
from gridcast.pipeline import METRICS, comparable_rows
from gridcast.store import (StoredModel, get_champion, live_vs_actual, load_artifact, pack_model,
                            promote, prune_artifacts, read_demand, read_weather, register_model,
                            save_artifact, save_forecasts, upsert_weather, weather_days_missing)
from gridcast.weather import (WeatherError, WeatherRateLimitError, build_weighted_weather, load_weather,
                              make_throttle, resolve_run, weather_table_path)

from gridcast.weather import (
    build_weighted_weather,
    make_throttle,
    resolve_run,
    WeatherError,
    WeatherRateLimitError,
)

DEFAULTS = {"model_prefix": "lgbm_weather", "min_rows": 20000, "max_staleness_days": 3,
            "min_weather_coverage": 0.95, "min_prev_day_coverage": 0.9, "recent_folds": 3,
            "recent_days": 28, "promote_tolerance": 0.02, "min_live_points": 672}


class DataValidationError(RuntimeError):
    pass


def jobs_cfg(cfg):
    return {**DEFAULTS, **cfg.get("jobs", {})}


def now_local(cfg) -> pd.Timestamp:
    return pd.Timestamp.now(tz=cfg["weather"]["timezone"]).tz_localize(None)


# ---------------------------------------------------------------- ingest + weather
def ingest(engine, cfg) -> int:
    from gridcast.scraper import fetch_latest

    df = fetch_latest()

    target = cfg["forecast"]["target"]

    if df is None or df.empty or target not in df.columns:
        raise DataValidationError("Scraper returned no usable demand data.")

    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise DataValidationError(
            "Scraper output is not sorted and unique."
        )

    count = upsert_raw(df, engine=engine)

    print(f"Demand ingestion: {count} rows upserted.")

    return count



def sync_weather(engine, cfg, extra_days=()) -> list:
    """
    Fetch target-day weather from Open-Meteo.
    If fetching fails, copy the latest complete stored weather day.

    Fallback records are marked with their source date in the log.
    """
    days = sorted(
        set(weather_days_missing(engine))
        | {pd.Timestamp(d).normalize() for d in extra_days}
    )

    throttle = make_throttle(cfg["weather"])
    failures = []

    for day in days:
        day = pd.Timestamp(day).normalize()

        try:
            print(f"Fetching Open-Meteo weather for {day.date()}...")

            weather = build_weighted_weather(
                day, cfg, throttle=throttle
            )

            if weather is None or weather.empty:
                raise WeatherError("Open-Meteo returned empty weather data.")

            if len(weather) != 96:
                raise WeatherError(
                    f"Expected 96 weather rows, received {len(weather)}."
                )

            run_ts = resolve_run(day, cfg["weather"])[1]

            upsert_weather(
                engine,
                weather.tz_localize(None),
                run_ts,
            )

            print(f"Saved Open-Meteo weather for {day.date()}.")

        except WeatherRateLimitError as exc:
            print(f"Open-Meteo rate limit: {exc}")
            failures.append((day, str(exc)))

        except (WeatherError, ValueError) as exc:
            print(f"Open-Meteo failed for {day.date()}: {exc}")

            try:
                fallback = load_latest_complete_weather(
                    engine, day
                )

                if fallback is None:
                    raise WeatherError(
                        "No previous complete weather day available."
                    )

                source_day, fallback_weather = fallback
                age = (day - source_day).days

                max_age = cfg["weather"].get("max_fallback_age_days", 2)

                if age > max_age:
                    raise WeatherError(
                        f"Fallback weather is too old: {age} days "
                        f"(maximum allowed: {max_age})."
                    )

                run_ts = resolve_run(day, cfg["weather"])[1]

                upsert_weather(
                    engine,
                    fallback_weather,
                    run_ts,
                )

                age = (day - source_day).days

                print(
                    f"Used fallback weather from {source_day.date()} "
                    f"for {day.date()} (age: {age} days)."
                )

            except Exception as fallback_exc:
                message = (
                    f"Open-Meteo failed: {exc}; "
                    f"fallback failed: {fallback_exc}"
                )
                print(message)
                failures.append((day, message))

    print(
        f"Weather sync: {len(days)} day(s) attempted, "
        f"{len(failures)} failed."
    )

    return failures

# ---------------------------------------------------------------- data + validation
def build_dataset(engine, cfg):
    df = make_regular(read_demand(engine), cfg["data"]["freq"], cfg["data"]["max_gap_steps"])
    weather = read_weather(engine) if cfg["features"].get("use_weather") else None
    return df, weather


def validate_inputs(df, weather, cfg, now) -> None:
    j, problems = jobs_cfg(cfg), []
    n = int(df[cfg["forecast"]["target"]].notna().sum())
    if n < j["min_rows"]:
        problems.append(f"only {n} demand rows (min {j['min_rows']})")
    stale = (now.normalize() - df.index.max().normalize()).days
    if stale > j["max_staleness_days"]:
        problems.append(f"latest demand is {stale} days old (max {j['max_staleness_days']})")
    if weather is not None:
        cov = float(df.index.isin(weather.index).mean())
        if cov < j["min_weather_coverage"]:
            problems.append(f"weather covers {cov:.1%} of demand rows (min {j['min_weather_coverage']:.0%})")
    if problems:
        raise DataValidationError("; ".join(problems))


def decide_promotion(ch_mape, base_mape, champ_live_mape=None, tol=0.02):
    reasons = []
    if not ch_mape < base_mape:
        reasons.append(f"not better than baseline ({ch_mape:.2f}% vs {base_mape:.2f}%)")
    if champ_live_mape is not None and ch_mape > champ_live_mape * (1 + tol):
        reasons.append(f"worse than champion's live forecasts ({ch_mape:.2f}% vs {champ_live_mape:.2f}%)")
    return (not reasons), reasons


# ---------------------------------------------------------------- weekly: retrain
def retrain(engine, cfg, now=None) -> dict:
    j, now = jobs_cfg(cfg), (now if now is not None else now_local(cfg))
    df, weather = build_dataset(engine, cfg)
    validate_inputs(df, weather, cfg, now)

    X, y = build_features(df, cfg, weather=weather)
    mask = comparable_rows(X, cfg)
    t, seed = cfg["forecast"]["target"], cfg["seed"]

    cfg_bt = copy.deepcopy(cfg)                          # recent window only: fast and relevant
    cfg_bt["backtest"].update(n_folds=j["recent_folds"], test_days=j["recent_days"])
    res, preds = run_backtest(LightGBMModel(seed=seed), X, y, cfg_bt, eval_mask=mask)
    base_res, _ = run_backtest(Blend([f"{t}_lag96", f"{t}_lag672"]), X, y, cfg_bt, eval_mask=mask)
    m, base = res[METRICS].mean(), base_res[METRICS].mean()

    final = LightGBMModel(seed=seed).fit(X, y)           # final model sees ALL data
    model_id = f"{j['model_prefix']}_{now:%Y%m%d}"
    args = dict(train_start=X.index.min(), train_end=X.index.max(), n_rows=len(X), config=cfg)
    register_model(engine, model_id, "lightgbm", "weather", metrics=m, **args)
    register_model(engine, "blend_1d_1w", "baseline", "demand_lags", metrics=base, **args)
    save_artifact(engine, model_id, pack_model(final, {"model_id": model_id, "trained_at": str(now)}))
    save_forecasts(engine, model_id, "backtest", preds["pred"])

    ch_mape, champ_live = float(m["mape"]), None
    champ = get_champion(engine)
    if champ and champ != model_id:
        live = live_vs_actual(engine, champ, preds.index.min())
        common = live.index.intersection(preds.index)
        if len(common) >= j["min_live_points"]:          # like-for-like: same days, both out-of-sample
            champ_live = mape(live.loc[common, "actual_mw"].to_numpy(), live.loc[common, "yhat_mw"].to_numpy())
            ch_mape = mape(preds.loc[common, "y"].to_numpy(), preds.loc[common, "pred"].to_numpy())

    ok, reasons = decide_promotion(ch_mape, float(base["mape"]), champ_live, j["promote_tolerance"])
    if ok:
        promote(engine, model_id)
    prune_artifacts(engine)
    print(f"{model_id}: promoted={ok} mape={ch_mape:.2f} baseline={float(base['mape']):.2f} {reasons}")
    return {"model_id": model_id, "promoted": ok, "reasons": reasons,
            "mape": ch_mape, "baseline_mape": float(base["mape"]), "champion_live_mape": champ_live}


# ---------------------------------------------------------------- daily: forecast
def forecast_day(engine, cfg, day, model_id=None, now=None) -> pd.Series:
    j, now = jobs_cfg(cfg), (now if now is not None else now_local(cfg))
    day = pd.Timestamp(day).normalize()
    model_id = model_id or get_champion(engine)
    if model_id is None:
        raise RuntimeError("No champion model yet. Run the weekly job first.")
    model = StoredModel(load_artifact(engine, model_id))

    df, weather = build_dataset(engine, cfg)
    if df.index.max() >= day:
        raise ValueError(f"Demand already exists for {day.date()}; refusing to 'forecast' the past.")
    if weather is None or not (weather.index.normalize() == day).any():
        raise DataValidationError(f"No weather stored for {day.date()}.")

    prev = df.loc[day - pd.Timedelta(days=1): day - pd.Timedelta(minutes=15), cfg["forecast"]["target"]]
    coverage = float(prev.notna().mean()) if len(prev) else 0.0
    if coverage < j["min_prev_day_coverage"]:
        raise DataValidationError(f"Only {coverage:.0%} of yesterday's demand is present (min {j['min_prev_day_coverage']:.0%}).")

    future = pd.date_range(day, periods=96, freq="15min", name="datetime")
    grid = pd.date_range(df.index.min(), future[-1], freq=cfg["data"]["freq"], name="datetime")
    X, _ = build_features(df.reindex(grid), cfg, weather=weather, drop_unlabeled=False)
    Xd = X.loc[X.index.isin(future)]
    if len(Xd) != 96:
        raise RuntimeError(f"Expected 96 feature rows for {day.date()}, got {len(Xd)}.")

    yhat = pd.Series(model.predict(Xd), index=Xd.index)
    save_forecasts(engine, model_id, "live", yhat, issued_at=now)
    print(f"{model_id}: saved live forecast for {day.date()} (peak {yhat.max():,.0f} MW at {yhat.idxmax():%H:%M})")
    return yhat


# ---------------------------------------------------------------- orchestration
def run_daily(engine, cfg):
    ingest(engine, cfg)
    today = now_local(cfg).normalize()
    sync_weather(engine, cfg, extra_days=[today])
    return forecast_day(engine, cfg, today)


def run_weekly(engine, cfg):
    ingest(engine, cfg)
    sync_weather(
        engine,
        cfg,
        extra_days=[now_local(cfg).normalize()]
    )
    return retrain(engine, cfg)


def bootstrap(engine, cfg):
    """One-time: load the existing CSV and weather parquet into the DB (no API calls)."""
    n = upsert_raw(load_raw(Path(cfg["root"]) / cfg["data"]["raw_path"]))
    wn, path = 0, weather_table_path(cfg)
    if path.exists():
        w = load_weather(path)
        for day, g in w.groupby(w.index.normalize()):
            if len(g) == 96:
                wn += upsert_weather(engine, g, resolve_run(day, cfg["weather"])[1])
    return n, wn



def load_latest_complete_weather(engine, target_day):
    """
    Return (source_day, weather_dataframe) for the latest
    complete weather day before target_day.
    """
    from sqlalchemy import text

    target_day = pd.Timestamp(target_day).normalize()

    query = text("""
        SELECT
            ts,
            temp_c,
            humidity,
            apparent_c,
            dew_point_c
        FROM weather_forecast
        WHERE ts < :target_day
        ORDER BY ts DESC
        LIMIT 2000
    """)

    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {"target_day": target_day.to_pydatetime()},
        ).fetchall()

    if not rows:
        return None

    df = pd.DataFrame(
        rows,
        columns=[
            "ts",
            "temp_c",
            "humidity",
            "apparent_c",
            "dew_point_c",
        ],
    )

    df["ts"] = pd.to_datetime(df["ts"])

    df = df.sort_values("ts")

    df["weather_day"] = df["ts"].dt.normalize()

    # Search newest source day first.
    for source_day in sorted(
        df["weather_day"].unique(), reverse=True
    ):
        day_df = df[df["weather_day"] == source_day].copy()

        day_df = day_df.sort_values("ts")

        if len(day_df) != 96:
            continue

        expected = pd.date_range(
            start=pd.Timestamp(source_day),
            periods=96,
            freq="15min",
        )

        actual = pd.DatetimeIndex(day_df["ts"])

        if not actual.equals(expected):
            continue

        day_df = day_df.set_index("ts")

        weather = day_df[
            ["temp_c", "humidity", "apparent_c", "dew_point_c"]
        ].copy()

        weather.index.name = "ts"

        return pd.Timestamp(source_day), weather

    return None



def weather_days_missing(engine):
    """
    Return missing weather dates from the day after the
    latest complete weather date through yesterday.
    """
    from sqlalchemy import text

    tz = "Asia/Kolkata"

    with engine.connect() as conn:
        latest = conn.execute(text("""
            SELECT MAX(weather_day)
            FROM (
                SELECT
                    DATE(ts) AS weather_day,
                    COUNT(*) AS n
                FROM weather_forecast
                GROUP BY DATE(ts)
                HAVING COUNT(*) = 96
            ) complete_days
        """)).scalar()

    if latest is None:
        return []

    # Database DATE is timezone-naive.
    latest = pd.Timestamp(latest).tz_localize(None).normalize()

    # Convert local date to a timezone-naive calendar date too.
    today = pd.Timestamp.now(tz=tz).tz_localize(None).normalize()
    yesterday = today - pd.Timedelta(days=1)

    if latest >= yesterday:
        return []

    return list(pd.date_range(
        start=latest + pd.Timedelta(days=1),
        end=yesterday,
        freq="D",
    ))
