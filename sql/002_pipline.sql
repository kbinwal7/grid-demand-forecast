ALTER TABLE weather_forecast ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT NOW();
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS n_rows INTEGER;
ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS config_json JSONB;

CREATE TABLE IF NOT EXISTS model_artifacts (
  model_id   TEXT PRIMARY KEY REFERENCES model_versions(model_id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  payload    BYTEA NOT NULL);

GRANT SELECT ON demand_raw, weather_forecast, forecasts, model_versions, forecast_vs_actual TO gridcast_reader;
REVOKE ALL ON model_artifacts FROM gridcast_reader;