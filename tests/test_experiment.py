import gzip
import json

import numpy as np
import pandas as pd
import pytest

from gridcast import experiment, jobs
from gridcast.models import (
    MODEL_REGISTRY,
    LightGBMModel,
    XGBoostModel,
    create_model,
)
from gridcast.store import StoredModel, pack_model, register_experiment


def _toy(n_days=55):
    index = pd.date_range(
        "2025-01-01", periods=n_days * 96, freq="15min", name="datetime"
    )
    interval = index.hour * 4 + index.minute // 15
    target = pd.Series(
        100 + 20 * np.sin(interval / 96 * 2 * np.pi),
        index=index,
        name="demand_met_mw",
    )
    features = pd.DataFrame(
        {
            "interval_of_day": interval,
            "noise": np.cos(interval / 96 * 2 * np.pi),
            "demand_met_mw_lag288": target.shift(288),
            "demand_met_mw_lag672": target.shift(672),
        },
        index=index,
    )
    return features, target


def _cfg():
    return {
        "seed": 7,
        "forecast": {"target": "demand_met_mw"},
        "features": {"use_weather": False, "demand_lags": [288, 672]},
        "backtest": {
            "n_folds": 2,
            "test_days": 3,
            "min_train_days": 20,
            "peak_start_hour": 18,
            "peak_end_hour": 22,
        },
        "experiments": {
            "output_dir": "experiments",
            "models": {
                "xgboost": {
                    "version": "test",
                    "val_days": 3,
                    "max_rounds": 30,
                    "patience": 5,
                    "params": {"n_estimators": 30, "max_depth": 2, "n_jobs": 1},
                }
            },
        },
        "root": ".",
    }


def test_model_registry_selects_lightgbm_and_xgboost():
    assert {"lightgbm", "xgboost"} <= set(MODEL_REGISTRY)
    assert isinstance(create_model("lightgbm"), LightGBMModel)
    assert isinstance(create_model("xgboost"), XGBoostModel)
    with pytest.raises(ValueError, match="Unsupported model algorithm"):
        create_model("unknown")


def test_xgboost_trains_with_missing_values_and_preserves_feature_order():
    X, y = _toy()
    train = X.index < "2025-02-10"
    model = XGBoostModel(
        params={"n_estimators": 30, "max_depth": 2, "n_jobs": 1},
        val_days=3,
        patience=5,
        seed=7,
    ).fit(X.loc[train], y.loc[train])

    ordered = model.predict(X.iloc[-96:])
    shuffled = model.predict(X.iloc[-96:][list(reversed(X.columns))])
    assert ordered.shape == (96,)
    assert np.isfinite(ordered).all()
    np.testing.assert_allclose(ordered, shuffled)


def test_xgboost_artifact_round_trip_and_lightgbm_legacy_artifact():
    X, y = _toy()
    model = XGBoostModel(
        params={"n_estimators": 20, "max_depth": 2, "n_jobs": 1},
        val_days=3,
        patience=3,
        seed=7,
    ).fit(X.iloc[:-96], y.iloc[:-96])
    metadata = {"model_id": "experiment_test"}
    packed = pack_model(model, metadata)
    stored = StoredModel(packed)
    np.testing.assert_allclose(
        model.predict(X.iloc[-96:]),
        stored.predict(X.iloc[-96:][list(reversed(X.columns))]),
    )
    assert stored.algorithm == "xgboost"
    assert stored.meta == metadata

    lightgbm = LightGBMModel(seed=7, max_rounds=20).fit(X.iloc[:-96], y.iloc[:-96])
    legacy_blob = pack_model(lightgbm, metadata)
    legacy_data = json.loads(gzip.decompress(legacy_blob))
    assert legacy_data["algorithm"] == "lightgbm"
    legacy_stored = StoredModel(legacy_blob)
    assert legacy_stored.algorithm == "lightgbm"
    np.testing.assert_allclose(
        lightgbm.predict(X.iloc[-96:]),
        legacy_stored.predict(X.iloc[-96:]),
    )


def test_experiment_compares_same_folds_and_never_promotes(
    monkeypatch, tmp_path
):
    X, y = _toy()
    cfg = _cfg()
    persisted = {}
    monkeypatch.setattr(
        experiment,
        "_champion_settings",
        lambda engine, seed: (
            "lightgbm_champion",
            lambda: LightGBMModel(
                seed=seed, max_rounds=30, val_days=3, patience=5
            ),
            None,
            "lightgbm",
        ),
    )
    monkeypatch.setattr(
        experiment,
        "register_experiment",
        lambda **kwargs: persisted.update(kwargs),
    )

    report = experiment.run_experiment_on_data(
        object(), cfg, X, y, output_dir=tmp_path
    )

    assert report["promoted"] is False
    assert report["candidate"]["algorithm"] == "xgboost"
    assert report["champion_comparison"]["model_id"] == "lightgbm_champion"
    assert len(report["candidate"]["folds"]) == cfg["backtest"]["n_folds"]
    assert len(report["champion_comparison"]["folds"]) == cfg["backtest"]["n_folds"]
    assert [row["test_start"] for row in report["candidate"]["folds"]] == [
        row["test_start"] for row in report["champion_comparison"]["folds"]
    ]
    for row in report["candidate"]["folds"]:
        assert set(row["metrics"]) == {"mape", "peak_mape", "mae", "mase", "bias"}
        assert row["training_seconds"] >= 0
        assert row["prediction_seconds"] >= 0
    assert persisted["algo"] == "xgboost"
    assert persisted["metadata"]["promoted"] is False
    assert persisted["metadata"]["status"] == "completed"
    assert persisted["metadata"]["artifact_reference"] == (
        f"model_artifacts:{report['model_id']}"
    )
    report_file = tmp_path / f"{report['model_id']}.json"
    assert report_file.exists()
    saved_report = json.loads(report_file.read_text(encoding="utf-8"))
    assert saved_report["report_path"] == str(report_file)
    assert isinstance(saved_report["candidate"]["folds"][0]["test_start"], str)


def test_experiment_registration_is_additive_and_non_champion():
    class Connection:
        def __init__(self):
            self.executions = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, params):
            self.executions.append((str(statement), params))

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def begin(self):
            return self.connection

    engine = Engine()
    metadata = {
        "trained_at": "2025-01-01T00:00:00+00:00",
        "metrics": {"mape": 1.0, "peak_mape": None, "mase": 0.9},
        "status": "completed",
        "promoted": False,
    }

    register_experiment(
        engine,
        "experiment_xgboost_test",
        "xgboost",
        "gridcast_features",
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-02-01"),
        100,
        metadata,
        b"artifact",
    )

    assert len(engine.connection.executions) == 2
    registration_sql, registration_params = engine.connection.executions[0]
    assert "is_champion" in registration_sql
    assert "FALSE" in registration_sql
    assert registration_params["model_id"] == "experiment_xgboost_test"
    artifact_sql, artifact_params = engine.connection.executions[1]
    assert "INSERT INTO model_artifacts" in artifact_sql
    assert artifact_params["artifact"] == b"artifact"


def test_experiment_model_cannot_write_production_forecasts(monkeypatch):
    monkeypatch.setattr(jobs, "get_champion", lambda engine: "lightgbm_production")
    monkeypatch.setattr(
        jobs,
        "get_model_record",
        lambda engine, model_id: {
            "config_json": {"status": "completed", "promoted": False}
        },
    )

    with pytest.raises(ValueError, match="cannot write production forecasts"):
        jobs.forecast_day(
            object(),
            {"jobs": {}},
            "2025-04-01",
            model_id="experiment_xgboost_test",
            now=pd.Timestamp("2025-03-31"),
        )
