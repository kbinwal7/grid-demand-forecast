"""Database I/O for the pipeline. Writes are idempotent (ON CONFLICT)."""
from __future__ import annotations

import gzip
import json

import lightgbm as lgb
import pandas as pd
from sqlalchemy import text

DEMAND_COLS = ["frequency_hz", "demand_met_mw", "nuclear_mw", "wind_mw", "solar_mw", "hydro_mw",
               "gas_mw", "thermal_mw", "others_mw", "net_demand_met_mw", "total_generation_mw",
               "net_exchange_mw"]
WEATHER_MAP = {"weighted_temperature_2m": "temp_c", "weighted_relative_humidity_2m": "humidity",
               "weighted_apparent_temperature": "apparent_c", "weighted_dew_point_2m": "dew_point_c"}


# ---------------------------------------------------------------- reads
def read_demand(engine, start=None) -> pd.DataFrame:
    """Same shape as data.load_raw(): sorted, unique, tz-naive 'datetime' index."""
    sql, params = f"SELECT ts AS datetime, {', '.join(DEMAND_COLS)} FROM demand_raw", {}
    if start is not None:
        sql += " WHERE ts >= :start"
        params["start"] = pd.Timestamp(start).to_pydatetime()
    df = pd.read_sql(text(sql + " ORDER BY ts"), engine, params=params)
    if df.empty:
        raise ValueError("demand_raw is empty")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.drop_duplicates("datetime").set_index("datetime").astype(float)
    return df.rename(columns={"net_exchange_mw": "net_transnational_exchange_mw"})


def read_weather(engine, start=None) -> pd.DataFrame:
    """Same shape as weather.load_weather(): tz-naive index, weighted_* columns."""
    sql, params = f"SELECT ts, {', '.join(WEATHER_MAP.values())} FROM weather_forecast", {}
    if start is not None:
        sql += " WHERE ts >= :start"
        params["start"] = pd.Timestamp(start).to_pydatetime()
    df = pd.read_sql(text(sql + " ORDER BY ts"), engine, params=params)
    if df.empty:
        raise ValueError("weather_forecast is empty")
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts").astype(float).rename(columns={v: k for k, v in WEATHER_MAP.items()})


def weather_days_missing(engine) -> list[pd.Timestamp]:
    """Days present in demand_raw that lack a complete (96-row) weather day."""
    q = text("""SELECT d.day FROM (SELECT DISTINCT ts::date AS day FROM demand_raw) d
                LEFT JOIN (SELECT ts::date AS day, COUNT(*) AS n FROM weather_forecast GROUP BY 1) w
                  USING (day) WHERE COALESCE(w.n, 0) < 96 ORDER BY d.day""")
    return [pd.Timestamp(d) for d in pd.read_sql(q, engine)["day"]]


def live_vs_actual(engine, model_id, start) -> pd.DataFrame:
    q = text("""SELECT ts, yhat_mw, actual_mw FROM forecast_vs_actual
                WHERE model_id = :m AND source = 'live' AND ts >= :s ORDER BY ts""")
    df = pd.read_sql(q, engine, params={"m": model_id, "s": pd.Timestamp(start).to_pydatetime()})
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts")


# ---------------------------------------------------------------- writes
def upsert_weather(engine, day_df: pd.DataFrame, run_ts) -> int:
    out = day_df.rename(columns=WEATHER_MAP)[list(WEATHER_MAP.values())].copy()
    out.index.name = "ts"
    out = out.reset_index()
    out["run_ts"] = pd.Timestamp(run_ts).to_pydatetime()
    cols = ["ts", "run_ts"] + list(WEATHER_MAP.values())
    sql = text(f"INSERT INTO weather_forecast ({', '.join(cols)}) VALUES "
               f"({', '.join(':' + c for c in cols)}) ON CONFLICT (ts) DO UPDATE SET "
               + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:]))
    records = out[cols].astype(object).to_dict("records")
    with engine.begin() as conn:
        conn.execute(sql, records)
    return len(records)


def register_model(engine, model_id, algo, feature_set, train_start, train_end, metrics, n_rows, config):
    sql = text("""INSERT INTO model_versions
        (model_id, trained_at, algo, feature_set, train_start, train_end,
         cv_mape, cv_peak_mape, cv_mase, n_rows, config_json)
        VALUES (:id, NOW(), :algo, :fs, :t0, :t1, :mape, :peak, :mase, :n, CAST(:cfg AS JSONB))
        ON CONFLICT (model_id) DO UPDATE SET trained_at = NOW(), train_end = EXCLUDED.train_end,
          cv_mape = EXCLUDED.cv_mape, cv_peak_mape = EXCLUDED.cv_peak_mape, cv_mase = EXCLUDED.cv_mase,
          n_rows = EXCLUDED.n_rows, config_json = EXCLUDED.config_json""")
    with engine.begin() as conn:
        conn.execute(sql, {"id": model_id, "algo": algo, "fs": feature_set,
                           "t0": pd.Timestamp(train_start).date(), "t1": pd.Timestamp(train_end).date(),
                           "mape": float(metrics["mape"]), "peak": float(metrics["peak_mape"]),
                           "mase": float(metrics["mase"]), "n": int(n_rows),
                           "cfg": json.dumps(config, default=str)})


def save_forecasts(engine, model_id, source, preds: pd.Series, issued_at=None) -> int:
    """preds: Series indexed by ts. For backtests issued_at defaults to the start of each day."""
    recs = [{"m": model_id, "s": source, "ts": ts.to_pydatetime(), "v": float(v),
             "i": (pd.Timestamp(issued_at) if issued_at is not None else ts.normalize()).to_pydatetime()}
            for ts, v in preds.items()]
    sql = text("""INSERT INTO forecasts (model_id, source, ts, issued_at, yhat_mw)
                  VALUES (:m, :s, :ts, :i, :v)
                  ON CONFLICT (model_id, source, ts) DO UPDATE
                  SET yhat_mw = EXCLUDED.yhat_mw, issued_at = EXCLUDED.issued_at""")
    with engine.begin() as conn:
        conn.execute(sql, recs)
    return len(recs)


# ---------------------------------------------------------------- model storage + champion
def pack_model(model, meta: dict) -> bytes:
    blob = {"columns": model.columns_, "booster": model.model_.booster_.model_to_string(), "meta": meta}
    return gzip.compress(json.dumps(blob, default=str).encode("utf-8"))


class StoredModel:
    """Loads a packed model. predict() reorders columns, so column order can't drift."""
    def __init__(self, blob: bytes):
        d = json.loads(gzip.decompress(blob).decode("utf-8"))
        self.columns_, self.meta = d["columns"], d["meta"]
        self._booster = lgb.Booster(model_str=d["booster"])

    def predict(self, X):
        return self._booster.predict(X[self.columns_])


def save_artifact(engine, model_id, blob: bytes) -> None:
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO model_artifacts (model_id, payload) VALUES (:m, :p)
                             ON CONFLICT (model_id) DO UPDATE SET payload = EXCLUDED.payload, created_at = NOW()"""),
                     {"m": model_id, "p": blob})


def load_artifact(engine, model_id) -> bytes:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT payload FROM model_artifacts WHERE model_id = :m"), {"m": model_id}).first()
    if row is None:
        raise KeyError(f"No stored model artifact for {model_id}")
    return bytes(row[0])


def get_champion(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT model_id FROM model_versions WHERE is_champion")).scalar()


def promote(engine, model_id) -> None:
    with engine.begin() as conn:                       # one transaction: never zero or two champions
        conn.execute(text("UPDATE model_versions SET is_champion = FALSE WHERE is_champion"))
        conn.execute(text("UPDATE model_versions SET is_champion = TRUE WHERE model_id = :m"), {"m": model_id})


def prune_artifacts(engine, keep: int = 5) -> None:
    with engine.begin() as conn:
        conn.execute(text("""DELETE FROM model_artifacts
            WHERE model_id NOT IN (SELECT model_id FROM model_versions ORDER BY trained_at DESC LIMIT :k)
              AND model_id NOT IN (SELECT model_id FROM model_versions WHERE is_champion)"""), {"k": keep})