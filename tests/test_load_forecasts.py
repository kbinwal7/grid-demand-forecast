import argparse
import sqlite3
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from scripts.load_forecasts import _parse_trained_at, load_forecasts, read_predictions


@pytest.fixture
def forecast_database(tmp_path):
    sqlite3.register_adapter(datetime, lambda value: value.isoformat(" "))
    database_path = tmp_path / "forecasts.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE demand_raw (
                ts TIMESTAMP PRIMARY KEY,
                demand_met_mw DOUBLE PRECISION
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE model_versions (
                model_id TEXT PRIMARY KEY,
                trained_at TIMESTAMP NOT NULL,
                algo TEXT NOT NULL,
                feature_set TEXT,
                cv_mape REAL,
                cv_peak_mape REAL,
                cv_mase REAL,
                is_champion BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE UNIQUE INDEX one_champion ON model_versions (is_champion)
            WHERE is_champion
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE forecasts (
                model_id TEXT NOT NULL REFERENCES model_versions(model_id),
                source TEXT NOT NULL,
                ts TIMESTAMP NOT NULL,
                issued_at TIMESTAMP NOT NULL,
                yhat_mw REAL NOT NULL,
                q10_mw REAL,
                q90_mw REAL,
                PRIMARY KEY (model_id, source, ts)
            )
            """
        )

        start = datetime(2024, 1, 1)
        history = [
            {"ts": start + timedelta(minutes=15 * i), "demand_met_mw": 100 + i // 96}
            for i in range(10 * 96)
        ]
        connection.execute(
            text(
                "INSERT INTO demand_raw (ts, demand_met_mw) "
                "VALUES (:ts, :demand_met_mw)"
            ),
            history,
        )
    yield engine
    engine.dispose()


def make_predictions(path):
    index = pd.DatetimeIndex(
        list(pd.date_range("2024-01-11 18:00", periods=16, freq="15min"))
        + list(pd.date_range("2024-01-12 18:00", periods=16, freq="15min")),
        name="ts",
    )
    predictions = pd.DataFrame(
        {
            "y": [110.0] * 16 + [111.0] * 16,
            "pred": [108.0] * 16 + [109.0] * 16,
            "fold": [0] * 16 + [1] * 16,
        },
        index=index,
    )
    predictions.to_parquet(path)
    return predictions


def test_loader_upserts_parquet_and_fold_metrics(forecast_database, tmp_path):
    path = tmp_path / "backtest.parquet"
    make_predictions(path)

    result = load_forecasts(
        "blend_1d_1w",
        path,
        champion=True,
        engine=forecast_database,
        trained_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    repeated = load_forecasts(
        "blend_1d_1w",
        path,
        champion=True,
        engine=forecast_database,
        trained_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    load_forecasts(
        "lgbm_demand_only",
        path,
        champion=True,
        engine=forecast_database,
        trained_at=datetime(2024, 1, 1, tzinfo=UTC),
    )

    assert result["rows"] == repeated["rows"] == 32
    assert result["cv_mape"] == pytest.approx((2 / 110 + 2 / 111) * 50)
    assert result["cv_peak_mape"] == pytest.approx(result["cv_mape"])
    assert result["cv_mase"] > 0

    with forecast_database.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM forecasts")).scalar_one()
            == 64
        )
        model = connection.execute(
            text(
                "SELECT algo, feature_set, is_champion FROM model_versions "
                "WHERE model_id = 'blend_1d_1w'"
            )
        ).one()
        assert model.algo == "blend"
        assert model.feature_set == "1-day and 1-week demand lags"
        assert not model.is_champion
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM model_versions WHERE is_champion")
            ).scalar_one()
            == 1
        )
        assert connection.execute(
            text(
                "SELECT is_champion FROM model_versions "
                "WHERE model_id = 'lgbm_demand_only'"
            )
        ).scalar_one()

        first_forecast = connection.execute(
            text(
                "SELECT ts, issued_at, yhat_mw, q10_mw, q90_mw "
                "FROM forecasts ORDER BY ts LIMIT 1"
            )
        ).one()
        assert pd.Timestamp(first_forecast.ts) == datetime(2024, 1, 11, 18)
        assert pd.Timestamp(first_forecast.issued_at) == datetime(2024, 1, 11)
        assert first_forecast.yhat_mw == 108.0
        assert first_forecast.q10_mw is None
        assert first_forecast.q90_mw is None


def test_parquet_contract_rejects_timezone_aware_index(tmp_path):
    path = tmp_path / "aware.parquet"
    index = pd.date_range("2024-01-01", periods=1, tz="Asia/Kolkata", name="ts")
    pd.DataFrame({"y": [1.0], "pred": [1.0], "fold": [0]}, index=index).to_parquet(path)

    with pytest.raises(ValueError, match="timezone-naive"):
        read_predictions(path)


def test_parquet_contract_rejects_missing_columns(tmp_path):
    path = tmp_path / "missing.parquet"
    index = pd.DatetimeIndex(["2024-01-01"], name="ts")
    pd.DataFrame({"y": [1.0], "pred": [1.0]}, index=index).to_parquet(path)

    with pytest.raises(ValueError, match="missing columns"):
        read_predictions(path)


def test_trained_at_requires_timezone():
    assert _parse_trained_at("2026-11-05T04:30:00Z").utcoffset().total_seconds() == 0
    with pytest.raises(argparse.ArgumentTypeError, match="timezone"):
        _parse_trained_at("2026-11-05T04:30:00")
