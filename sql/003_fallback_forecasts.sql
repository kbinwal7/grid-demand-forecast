-- Idempotent migration for separately stored fallback forecasts.
ALTER TABLE weather_forecast
  ADD COLUMN IF NOT EXISTS weather_source_day DATE;
ALTER TABLE weather_forecast
  ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE weather_forecast
  ADD COLUMN IF NOT EXISTS fallback_age_days INTEGER NOT NULL DEFAULT 0;

ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS n_rows INTEGER;
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS config_json JSONB;

CREATE TABLE IF NOT EXISTS fallback_forecasts (
  model_id TEXT NOT NULL REFERENCES model_versions(model_id),
  forecast_type TEXT NOT NULL CHECK (forecast_type IN ('backtest', 'live')),
  ts TIMESTAMP NOT NULL,
  issued_at TIMESTAMP NOT NULL,
  yhat_mw REAL NOT NULL,
  demand_source_day DATE NOT NULL,
  demand_age_days INTEGER NOT NULL,
  weather_is_fallback BOOLEAN NOT NULL DEFAULT FALSE,
  weather_fallback_age_days INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (model_id, forecast_type, ts)
);

CREATE INDEX IF NOT EXISTS fallback_forecasts_ts
  ON fallback_forecasts (ts);
