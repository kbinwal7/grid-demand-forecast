"""Offline experiments that share GridCast's production feature and fold logic."""

from __future__ import annotations

import argparse
import copy
import json
import math
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from gridcast import jobs
from gridcast.backtest import make_folds
from gridcast.features import build_features
from gridcast.metrics import mae, mape, mase
from gridcast.models import MODEL_REGISTRY, create_model
from gridcast.pipeline import comparable_rows
from gridcast.store import (
    get_champion,
    get_model_record,
    pack_model,
    register_experiment,
)

REPORT_VERSION = 1
METRIC_NAMES = ("mape", "peak_mape", "mae", "mase", "bias")


def _finite_or_none(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _score_fold(y_true, prediction, y_train, peak_hours):
    peak_start, peak_end = peak_hours
    peak_mask = (y_true.index.hour >= peak_start) & (y_true.index.hour < peak_end)
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "mape": _finite_or_none(mape(y_true.to_numpy(), prediction)),
            "peak_mape": (
                _finite_or_none(mape(y_true[peak_mask].to_numpy(), prediction[peak_mask]))
                if peak_mask.any()
                else None
            ),
            "mae": _finite_or_none(mae(y_true.to_numpy(), prediction)),
            "mase": _finite_or_none(mase(y_true, pd.Series(prediction, index=y_true.index), y_train)),
            "bias": _finite_or_none(np.mean(prediction - y_true.to_numpy())),
        }


def _timed_backtest(model_factory, X, y, folds, eval_mask, peak_hours):
    fold_results = []
    fold_predictions = []
    for fold_number, (train_index, test_index, test_start) in enumerate(folds):
        eval_index = (
            test_index
            if eval_mask is None
            else test_index[eval_mask.loc[test_index].to_numpy()]
        )
        if len(eval_index) == 0:
            raise ValueError(f"Backtest fold {fold_number} has no comparable evaluation rows.")

        model = model_factory()
        started = time.perf_counter()
        model.fit(X.loc[train_index], y.loc[train_index])
        training_seconds = time.perf_counter() - started

        started = time.perf_counter()
        predictions = np.asarray(model.predict(X.loc[eval_index]), dtype=float)
        prediction_seconds = time.perf_counter() - started
        if len(predictions) != len(eval_index):
            raise ValueError(
                f"Model returned {len(predictions)} predictions for "
                f"{len(eval_index)} evaluation rows in fold {fold_number}."
            )

        actual = y.loc[eval_index]
        peak_count = int(
            ((eval_index.hour >= peak_hours[0]) & (eval_index.hour < peak_hours[1])).sum()
        )
        fold_results.append(
            {
                "fold": fold_number,
                "train_start": train_index.min().date(),
                "train_end": train_index.max().date(),
                "test_start": test_start.date(),
                "test_end": test_index.max().date(),
                "training_rows": len(train_index),
                "test_rows": len(test_index),
                "evaluation_rows": len(eval_index),
                "peak_evaluation_rows": peak_count,
                "metrics": _score_fold(actual, predictions, y.loc[train_index], peak_hours),
                "training_seconds": training_seconds,
                "prediction_seconds": prediction_seconds,
            }
        )
        fold_predictions.append(
            pd.DataFrame(
                {
                    "actual": actual.to_numpy(),
                    "prediction": predictions,
                    "fold": fold_number,
                },
                index=eval_index,
            )
        )

    return fold_results, pd.concat(fold_predictions).sort_index()


def _aggregate_metrics(folds):
    weights = {
        "mape": "evaluation_rows",
        "peak_mape": "peak_evaluation_rows",
        "mae": "evaluation_rows",
        "mase": "evaluation_rows",
        "bias": "evaluation_rows",
    }
    aggregate = {}
    for metric in METRIC_NAMES:
        values = [
            (fold["metrics"][metric], fold[weights[metric]])
            for fold in folds
            if fold["metrics"][metric] is not None and fold[weights[metric]] > 0
        ]
        total_weight = sum(weight for _, weight in values)
        aggregate[metric] = (
            _finite_or_none(
                sum(value * weight for value, weight in values) / total_weight
            )
            if total_weight
            else None
        )
    return aggregate


def _model_settings(cfg, algorithm, overrides):
    settings = copy.deepcopy(
        cfg.get("experiments", {}).get("models", {}).get(algorithm, {})
    )
    version = str(settings.pop("version", "1"))
    if overrides:
        params = dict(settings.get("params", {}))
        params.update(overrides)
        settings["params"] = params
    return version, settings


def _champion_settings(engine, seed):
    champion_id = get_champion(engine)
    if not champion_id:
        return None, None, None, None
    record = get_model_record(engine, champion_id)
    if record is None:
        return champion_id, None, "Champion model metadata is missing.", None

    algorithm = str(record["algo"]).lower()
    if algorithm not in MODEL_REGISTRY:
        return (
            champion_id,
            None,
            f"Champion algorithm {algorithm!r} is not registered.",
            algorithm,
        )

    metadata = record.get("config_json") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    model_config = metadata.get("model_config", {})
    if algorithm == "lightgbm":
        model_config = {}
    return (
        champion_id,
        lambda: create_model(algorithm, model_config, seed=seed),
        None,
        algorithm,
    )


def run_experiment_on_data(
    engine,
    cfg,
    X,
    y,
    algorithm="xgboost",
    parameter_overrides=None,
    output_dir=None,
):
    """Run identical chronological folds against a candidate and the current champion."""
    algorithm = algorithm.lower()
    if algorithm not in MODEL_REGISTRY:
        raise ValueError(
            f"Unsupported model algorithm {algorithm!r}. "
            f"Choose from: {', '.join(sorted(MODEL_REGISTRY))}."
        )
    if not X.index.equals(y.index):
        raise ValueError("Feature and target indexes must match exactly.")

    mask = comparable_rows(X, cfg)
    backtest_cfg = cfg["backtest"]
    folds = make_folds(
        X.index,
        backtest_cfg["n_folds"],
        backtest_cfg["test_days"],
        backtest_cfg["min_train_days"],
    )
    peak_hours = (backtest_cfg["peak_start_hour"], backtest_cfg["peak_end_hour"])
    seed = cfg["seed"]
    version, model_config = _model_settings(cfg, algorithm, parameter_overrides)
    candidate_factory = lambda: create_model(algorithm, model_config, seed=seed)

    candidate_folds, _ = _timed_backtest(
        candidate_factory, X, y, folds, mask, peak_hours
    )
    (
        champion_id,
        champion_factory,
        comparison_note,
        champion_algorithm,
    ) = _champion_settings(engine, seed)
    champion_results = None
    if champion_factory is not None:
        champion_folds, _ = _timed_backtest(
            champion_factory, X, y, folds, mask, peak_hours
        )
        champion_results = {
            "model_id": champion_id,
            "algorithm": champion_algorithm,
            "metrics": _aggregate_metrics(champion_folds),
            "folds": champion_folds,
            "timing": {
                "training_seconds": sum(row["training_seconds"] for row in champion_folds),
                "prediction_seconds": sum(row["prediction_seconds"] for row in champion_folds),
            },
        }
    elif comparison_note is None:
        comparison_note = "No champion is registered."

    training_started = time.perf_counter()
    final_model = candidate_factory().fit(X, y)
    final_fit_seconds = time.perf_counter() - training_started

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    model_id = f"experiment_{algorithm}_{stamp}_{uuid.uuid4().hex[:8]}"
    metrics = _aggregate_metrics(candidate_folds)
    candidate_summary = {
        "model_id": model_id,
        "algorithm": algorithm,
        "version": version,
        "metrics": metrics,
        "folds": candidate_folds,
        "timing": {
            "training_seconds": sum(row["training_seconds"] for row in candidate_folds),
            "prediction_seconds": sum(row["prediction_seconds"] for row in candidate_folds),
            "final_fit_seconds": final_fit_seconds,
        },
    }
    report = {
        "report_version": REPORT_VERSION,
        "model_id": model_id,
        "status": "completed",
        "promoted": False,
        "promotion_note": "Experiments never promote models automatically.",
        "trained_at": now.isoformat(),
        "train_start": X.index.min().date().isoformat(),
        "train_end": X.index.max().date().isoformat(),
        "training_rows": len(X),
        "feature_config": copy.deepcopy(cfg["features"]),
        "feature_columns": list(X.columns),
        "model_config": model_config,
        "candidate": candidate_summary,
        "champion_comparison": champion_results,
        "comparison_note": comparison_note,
    }

    report["artifact_reference"] = f"model_artifacts:{model_id}"
    artifact = pack_model(final_model, report)
    register_experiment(
        engine=engine,
        model_id=model_id,
        algo=algorithm,
        feature_set="gridcast_features",
        train_start=X.index.min(),
        train_end=X.index.max(),
        n_rows=len(X),
        metadata=report,
        artifact=artifact,
    )

    report_dir = Path(
        output_dir
        or Path(cfg["root"])
        / cfg.get("experiments", {}).get("output_dir", "data/experiments")
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{model_id}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(
        json.dumps(report, indent=2, allow_nan=False, default=str),
        encoding="utf-8",
    )
    return report


def run_experiment(
    engine,
    cfg,
    algorithm="xgboost",
    parameter_overrides=None,
    output_dir=None,
):
    data, weather = jobs.build_dataset(engine, cfg)
    jobs.validate_inputs(data, weather, cfg, jobs.now_local(cfg))
    X, y = build_features(data, cfg, weather=weather)
    return run_experiment_on_data(
        engine,
        cfg,
        X,
        y,
        algorithm=algorithm,
        parameter_overrides=parameter_overrides,
        output_dir=output_dir,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run an offline GridCast model experiment.")
    parser.add_argument("--model", choices=sorted(MODEL_REGISTRY), required=True)
    parser.add_argument(
        "--params",
        help="JSON object of model hyperparameter overrides, e.g. '{\"max_depth\": 6}'.",
    )
    parser.add_argument("--output-dir", help="Override the experiment report directory.")
    args = parser.parse_args(argv)
    overrides = json.loads(args.params) if args.params else None
    if overrides is not None and not isinstance(overrides, dict):
        parser.error("--params must be a JSON object.")

    from gridcast.config import load_config
    from gridcast.db import get_engine

    cfg = load_config()
    engine = get_engine()
    try:
        report = run_experiment(
            engine,
            cfg,
            algorithm=args.model,
            parameter_overrides=overrides,
            output_dir=args.output_dir,
        )
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    main()
