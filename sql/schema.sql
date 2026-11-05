-- TIMESTAMP columns are timezone-naive and store local Indian Standard Time (IST).
CREATE TABLE IF NOT EXISTS demand_raw (
  ts TIMESTAMP PRIMARY KEY,
  frequency_hz DOUBLE PRECISION,
  demand_met_mw DOUBLE PRECISION,
  nuclear_mw DOUBLE PRECISION,
  wind_mw DOUBLE PRECISION,
  solar_mw DOUBLE PRECISION,
  hydro_mw DOUBLE PRECISION,
  gas_mw DOUBLE PRECISION,
  thermal_mw DOUBLE PRECISION,
  others_mw DOUBLE PRECISION,
  net_demand_met_mw DOUBLE PRECISION,
  total_generation_mw DOUBLE PRECISION,
  net_exchange_mw DOUBLE PRECISION,
  ingested_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'Asia/Kolkata')
);

CREATE TABLE IF NOT EXISTS weather_forecast (
  ts TIMESTAMP PRIMARY KEY,
  run_ts TIMESTAMPTZ,
  temp_c DOUBLE PRECISION,
  humidity DOUBLE PRECISION,
  apparent_c DOUBLE PRECISION,
  dew_point_c DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS model_versions (
  model_id TEXT PRIMARY KEY,
  trained_at TIMESTAMPTZ NOT NULL,
  algo TEXT NOT NULL,
  feature_set TEXT,
  train_start DATE,
  train_end DATE,
  cv_mape REAL,
  cv_peak_mape REAL,
  cv_mase REAL,
  is_champion BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS one_champion
  ON model_versions (is_champion) WHERE is_champion;

CREATE TABLE IF NOT EXISTS forecasts (
  model_id TEXT NOT NULL REFERENCES model_versions(model_id),
  source TEXT NOT NULL CHECK (source IN ('backtest', 'live')),
  ts TIMESTAMP NOT NULL,
  issued_at TIMESTAMP NOT NULL,
  yhat_mw REAL NOT NULL,
  q10_mw REAL,
  q90_mw REAL,
  PRIMARY KEY (model_id, source, ts)
);

CREATE INDEX IF NOT EXISTS forecasts_ts ON forecasts (ts);

CREATE OR REPLACE VIEW forecast_vs_actual AS
SELECT
  f.model_id,
  f.source,
  f.ts,
  f.yhat_mw,
  f.q10_mw,
  f.q90_mw,
  d.demand_met_mw AS actual_mw,
  f.yhat_mw - d.demand_met_mw AS err_mw,
  ABS(f.yhat_mw - d.demand_met_mw) / NULLIF(d.demand_met_mw, 0) * 100 AS ape
FROM forecasts f
JOIN demand_raw d ON d.ts = f.ts;
