#!/usr/bin/env python
"""Load notebook backtest predictions and their CV metrics into PostgreSQL."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from gridcast import metrics

PEAK_HOURS = (18, 22)
REQUIRED_COLUMNS = {"y", "pred", "fold"}
MODEL_METADATA = {
    "blend_1d_1w": ("blend", "1-day and 1-week demand lags"),
    "lgbm_demand_only": ("lightgbm", "demand-only"),
    "lgbm_weather": ("lightgbm", "demand and weather"),
}


def read_predictions(path: str | Path) -> pd.DataFrame:
    predictions = pd.read_parquet(path)
    if not isinstance(predictions.index, pd.DatetimeIndex):
        raise TypeError("Predictions parquet must have a DatetimeIndex named 'ts'.")
    if predictions.index.name != "ts":
        raise ValueError("Predictions parquet DatetimeIndex must be named 'ts'.")
    if predictions.index.tz is not None:
        raise ValueError("Prediction timestamps must be timezone-naive local IST.")

    missing = REQUIRED_COLUMNS - set(predictions.columns)
    if missing:
        raise ValueError(f"Predictions parquet is missing columns: {sorted(missing)}")
    if predictions.index.hasnans or predictions.index.has_duplicates:
        raise ValueError("Prediction timestamps must be non-null and unique.")

    predictions = predictions.sort_index()
    for column in ("y", "pred"):
        predictions[column] = pd.to_numeric(predictions[column], errors="coerce")
        if not np.isfinite(predictions[column].to_numpy(dtype=float)).all():
            raise ValueError(
                f"Predictions column {column!r} must contain finite values."
            )
    if predictions["y"].eq(0).any():
        raise ValueError("Actual demand must be nonzero to calculate MAPE.")
    if predictions["fold"].isna().any():
        raise ValueError("Prediction fold values must not be null.")
    return predictions


def _fold_metrics(
    connection,
    fold_predictions: pd.DataFrame,
) -> dict[str, float]:
    fold_start = fold_predictions.index.min().to_pydatetime()
    training_rows = connection.execute(
        text(
            "SELECT ts, demand_met_mw FROM demand_raw "
            "WHERE ts < :fold_start AND demand_met_mw IS NOT NULL ORDER BY ts"
        ),
        {"fold_start": fold_start},
    ).all()
    if not training_rows:
        raise ValueError(
            f"No historical demand exists before fold starting {fold_start}."
        )

    y_train = pd.Series(
        [row.demand_met_mw for row in training_rows],
        index=pd.DatetimeIndex([row.ts for row in training_rows]),
        dtype=float,
    )
    y = fold_predictions["y"]
    pred = fold_predictions["pred"]
    peak_mask = (y.index.hour >= PEAK_HOURS[0]) & (y.index.hour < PEAK_HOURS[1])
    if not peak_mask.any():
        raise ValueError(
            f"Fold starting {fold_start} has no predictions in the 18:00-22:00 peak window."
        )

    seasonal_scale = y_train.sub(y_train.shift(96, freq="15min")).dropna().abs().mean()
    if not np.isfinite(seasonal_scale) or seasonal_scale == 0:
        raise ValueError(
            f"Historical demand before fold starting {fold_start} cannot provide a "
            "finite, nonzero daily seasonal MASE scale."
        )

    values = {
        "mape": metrics.mape(y, pred),
        "peak_mape": metrics.mape(y[peak_mask], pred[peak_mask]),
        "mase": metrics.mase(y, pred, y_train),
    }
    if not all(np.isfinite(value) for value in values.values()):
        raise ValueError(f"Fold starting {fold_start} produced a non-finite CV metric.")
    return values


def calculate_cv_metrics(predictions: pd.DataFrame, engine: Engine) -> dict[str, float]:
    """Average fold metrics; each fold's MASE uses only earlier demand_raw data."""
    fold_results = []
    with engine.connect() as connection:
        for _, fold in predictions.groupby("fold", sort=False):
            fold_results.append(_fold_metrics(connection, fold))

    return {
        "cv_mape": float(np.mean([result["mape"] for result in fold_results])),
        "cv_peak_mape": float(
            np.mean([result["peak_mape"] for result in fold_results])
        ),
        "cv_mase": float(np.mean([result["mase"] for result in fold_results])),
    }


def _forecast_records(model_id: str, predictions: pd.DataFrame) -> list[dict]:
    records = []
    for timestamp, row in predictions.iterrows():
        ts = timestamp.to_pydatetime()
        records.append(
            {
                "model_id": model_id,
                "source": "backtest",
                "ts": ts,
                "issued_at": ts.replace(hour=0, minute=0, second=0, microsecond=0),
                "yhat_mw": float(row["pred"]),
                "q10_mw": None,
                "q90_mw": None,
            }
        )
    return records


def load_forecasts(
    model_id: str,
    preds_path: str | Path,
    source: str = "backtest",
    champion: bool = False,
    engine: Engine | None = None,
    *,
    trained_at: datetime | None = None,
) -> dict[str, float | int]:
    if source != "backtest":
        raise ValueError("The parquet input contract supports source='backtest' only.")
    if model_id not in MODEL_METADATA:
        raise ValueError(f"Unsupported model id: {model_id!r}")
    if (
        trained_at is None
        or trained_at.tzinfo is None
        or trained_at.utcoffset() is None
    ):
        raise ValueError("trained_at must be provided as a timezone-aware timestamp.")

    predictions = read_predictions(preds_path)
    if predictions.empty:
        raise ValueError("Predictions parquet contains no rows.")

    if engine is None:
        from gridcast.db import get_engine

        engine = get_engine()

    cv = calculate_cv_metrics(predictions, engine)
    algo, feature_set = MODEL_METADATA[model_id]
    with engine.begin() as connection:
        if champion:
            connection.execute(
                text("UPDATE model_versions SET is_champion = FALSE WHERE is_champion")
            )

        connection.execute(
            text(
                """
                INSERT INTO model_versions (
                    model_id, trained_at, algo, feature_set,
                    cv_mape, cv_peak_mape, cv_mase, is_champion
                )
                VALUES (
                    :model_id, :trained_at, :algo, :feature_set,
                    :cv_mape, :cv_peak_mape, :cv_mase, :is_champion
                )
                ON CONFLICT (model_id) DO UPDATE SET
                    trained_at = EXCLUDED.trained_at,
                    algo = EXCLUDED.algo,
                    feature_set = EXCLUDED.feature_set,
                    cv_mape = EXCLUDED.cv_mape,
                    cv_peak_mape = EXCLUDED.cv_peak_mape,
                    cv_mase = EXCLUDED.cv_mase,
                    is_champion = model_versions.is_champion OR EXCLUDED.is_champion
                """
            ),
            {
                "model_id": model_id,
                "trained_at": trained_at.astimezone(UTC),
                "algo": algo,
                "feature_set": feature_set,
                **cv,
                "is_champion": champion,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO forecasts (
                    model_id, source, ts, issued_at, yhat_mw, q10_mw, q90_mw
                )
                VALUES (
                    :model_id, :source, :ts, :issued_at, :yhat_mw, :q10_mw, :q90_mw
                )
                ON CONFLICT (model_id, source, ts) DO UPDATE SET
                    issued_at = EXCLUDED.issued_at,
                    yhat_mw = EXCLUDED.yhat_mw,
                    q10_mw = EXCLUDED.q10_mw,
                    q90_mw = EXCLUDED.q90_mw
                """
            ),
            _forecast_records(model_id, predictions),
        )

    return {"rows": len(predictions), **cv}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", required=True, choices=sorted(MODEL_METADATA))
    parser.add_argument("--preds", required=True, type=Path)
    parser.add_argument("--source", choices=["backtest"], default="backtest")
    parser.add_argument("--champion", action="store_true")
    parser.add_argument(
        "--trained-at",
        required=True,
        type=_parse_trained_at,
        help="model training timestamp in ISO 8601 with timezone, e.g. 2026-11-05T04:30:00Z",
    )
    args = parser.parse_args()

    result = load_forecasts(
        args.model_id,
        args.preds,
        args.source,
        args.champion,
        trained_at=args.trained_at,
    )
    print(
        f"Loaded {result['rows']} {args.source} forecasts for {args.model_id}; "
        f"CV MAPE={result['cv_mape']:.4f}, "
        f"peak MAPE={result['cv_peak_mape']:.4f}, "
        f"MASE={result['cv_mase']:.4f}"
    )


def _parse_trained_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "trained-at must be an ISO 8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "trained-at must include a timezone, for example +05:30 or Z."
        )
    return parsed


if __name__ == "__main__":
    main()
