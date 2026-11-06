"""Fill the database with ~90 days of realistic *synthetic* data so the dashboard has
something to show without running the scraper / training pipeline.

    python scripts/seed_demo.py           # refuses if demand_raw already has rows
    python scripts/seed_demo.py --force   # upsert over existing rows

Writes: demand_raw (actuals up to yesterday), weather_forecast, two models
(baseline `blend_1d_1w` + champion `lgbm_demo`), backtest forecasts for past days
and a live forecast for today + tomorrow.
"""

import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from gridcast.db import get_engine

HISTORY_DAYS = 90
BACKTEST_DAYS = 60
STEP = "15min"
rng = np.random.default_rng(42)


def synthetic_temperature(idx: pd.DatetimeIndex) -> np.ndarray:
    doy = idx.dayofyear.to_numpy()
    hour = idx.hour.to_numpy() + idx.minute.to_numpy() / 60
    seasonal = 27 + 7 * np.sin(2 * np.pi * (doy - 100) / 365)
    diurnal = 6 * np.sin(2 * np.pi * (hour - 9) / 24)
    day_noise = pd.Series(rng.normal(0, 1.5, len(idx)), index=idx).groupby(idx.date).transform("mean")
    return seasonal + diurnal + day_noise.to_numpy()


def synthetic_demand(idx: pd.DatetimeIndex, temp: np.ndarray) -> np.ndarray:
    hour = idx.hour.to_numpy() + idx.minute.to_numpy() / 60
    shape = (
        0.82
        + 0.10 * np.exp(-((hour - 11) ** 2) / 8)  # daytime load
        + 0.16 * np.exp(-((hour - 20) ** 2) / 4)  # evening peak
        - 0.06 * np.exp(-((hour - 4) ** 2) / 6)  # night trough
    )
    weekly = np.where(idx.dayofweek.to_numpy() == 6, 0.93, np.where(idx.dayofweek.to_numpy() == 5, 0.97, 1.0))
    cooling = 1 + 0.012 * np.clip(temp - 24, 0, None)
    noise = rng.normal(1, 0.008, len(idx))
    return 190_000 * shape * weekly * cooling * noise


def upsert(conn, table: str, keys: list[str], rows: list[dict]) -> None:
    cols = list(rows[0])
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    conn.execute(
        text(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
        ),
        rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="upsert even if demand_raw has data")
    args = parser.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT COUNT(*) FROM demand_raw")).scalar_one()
    if existing and not args.force:
        raise SystemExit(f"demand_raw already has {existing} rows; re-run with --force to overwrite.")

    today = pd.Timestamp(datetime.now()).normalize()
    start = today - pd.Timedelta(days=HISTORY_DAYS)
    full_idx = pd.date_range(start, today + pd.Timedelta(days=2), freq=STEP, inclusive="left")
    temp = synthetic_temperature(full_idx)
    demand = pd.Series(synthetic_demand(full_idx, temp), index=full_idx)
    actual = demand[demand.index < today]  # actuals published up to yesterday

    # Forecasts --------------------------------------------------------------
    fc_idx = pd.date_range(today - pd.Timedelta(days=BACKTEST_DAYS), today + pd.Timedelta(days=2), freq=STEP, inclusive="left")
    truth = demand.loc[fc_idx]
    day_err = pd.Series(rng.normal(0, 0.018, len(fc_idx)), index=fc_idx).groupby(fc_idx.date).transform("mean")
    evening = np.where((fc_idx.hour >= 18) & (fc_idx.hour < 22), 1.012, 1.0)  # slight evening over-forecast
    champion = truth * (1 + day_err) * evening * rng.normal(1, 0.006, len(fc_idx))
    spread = 0.032 * champion
    lag1 = demand.shift(96).loc[fc_idx]
    lag7 = demand.shift(672).loc[fc_idx]
    baseline = ((lag1 + lag7) / 2).fillna(truth)

    def rows(model_id: str, yhat: pd.Series, source: str, q=True):
        return [
            {
                "model_id": model_id,
                "source": source,
                "ts": ts.to_pydatetime(),
                "issued_at": (ts.normalize() - timedelta(days=1) + timedelta(hours=10)).to_pydatetime(),
                "yhat_mw": float(v),
                "q10_mw": float(v - 1.28 * s) if q else None,
                "q90_mw": float(v + 1.28 * s) if q else None,
            }
            for ts, v, s in zip(yhat.index, yhat.to_numpy(), spread.loc[yhat.index].to_numpy())
        ]

    past = fc_idx < today
    forecast_rows = (
        rows("lgbm_demo", champion[past], "backtest")
        + rows("lgbm_demo", champion[~past], "live")
        + rows("blend_1d_1w", baseline[past], "backtest", q=False)
    )

    models = [
        {"model_id": "blend_1d_1w", "algo": "blend", "feature_set": "1-day and 1-week demand lags",
         "cv_mape": 4.6, "cv_peak_mape": 5.4, "cv_mase": 1.0, "is_champion": False},
        {"model_id": "lgbm_demo", "algo": "lightgbm", "feature_set": "demand lags + weather (demo)",
         "cv_mape": 2.1, "cv_peak_mape": 2.6, "cv_mase": 0.45, "is_champion": True},
    ]
    for m in models:
        m.update(trained_at=datetime.now().astimezone(), train_start=start.date(),
                 train_end=(today - pd.Timedelta(days=BACKTEST_DAYS + 1)).date())

    with engine.begin() as conn:
        upsert(conn, "demand_raw", ["ts"], [
            {"ts": ts.to_pydatetime(), "demand_met_mw": float(v), "frequency_hz": 50.0}
            for ts, v in actual.items()
        ])
        upsert(conn, "weather_forecast", ["ts"], [
            {"ts": ts.to_pydatetime(), "temp_c": float(t)} for ts, t in zip(full_idx, temp)
        ])
        conn.execute(text("UPDATE model_versions SET is_champion = FALSE WHERE is_champion"))
        upsert(conn, "model_versions", ["model_id"], models)
        upsert(conn, "forecasts", ["model_id", "source", "ts"], forecast_rows)

    print(f"Seeded {len(actual):,} demand rows, {len(full_idx):,} weather rows, "
          f"{len(forecast_rows):,} forecast rows, champion = lgbm_demo.")


if __name__ == "__main__":
    main()
