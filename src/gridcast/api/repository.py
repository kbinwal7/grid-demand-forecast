"""Read-only SQL queries used by the API. One method per dashboard need."""

from datetime import date, datetime, time, timedelta

from sqlalchemy import Engine, text

# When a model has both a live and a backtest forecast for the same interval,
# the live one wins. Every accuracy query starts from this de-duplicated set.
SCORED_CTE = """
WITH scored AS (
    SELECT DISTINCT ON (ts) ts, source, yhat_mw, q10_mw, q90_mw, actual_mw, err_mw, ape
    FROM forecast_vs_actual
    WHERE model_id = :model_id
      AND ts >= :start_ts AND ts < :end_ts
    ORDER BY ts, (source = 'live') DESC
)
"""


def _day_bounds(start: date, end: date) -> dict:
    return {
        "start_ts": datetime.combine(start, time.min),
        "end_ts": datetime.combine(end + timedelta(days=1), time.min),
    }


class ForecastRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _all(self, sql: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]

    def _one(self, sql: str, params: dict | None = None) -> dict:
        rows = self._all(sql, params)
        return rows[0] if rows else {}

    # ------------------------------------------------------------ models
    def models(self) -> list[dict]:
        return self._all(
            """
            SELECT model_id, trained_at, algo, feature_set, train_start, train_end,
                   cv_mape, cv_peak_mape, cv_mase, is_champion
            FROM model_versions
            ORDER BY is_champion DESC, trained_at DESC, model_id
            """
        )

    def latest_forecast_date(self, model_id: str) -> date | None:
        row = self._one(
            "SELECT MAX(ts)::date AS day FROM forecasts WHERE model_id = :model_id",
            {"model_id": model_id},
        )
        return row.get("day")

    def last_scored_date(self, model_id: str, on_or_before: date) -> date | None:
        row = self._one(
            """
            SELECT MAX(ts)::date AS day FROM forecast_vs_actual
            WHERE model_id = :model_id AND ts < :end_ts
            """,
            {"model_id": model_id, "end_ts": datetime.combine(on_or_before + timedelta(days=1), time.min)},
        )
        return row.get("day")

    # ------------------------------------------------------------ one day
    def forecast_day(self, model_id: str, day: date) -> list[dict]:
        """96 forecast intervals for one day, with the actual where it is known."""
        return self._all(
            """
            WITH chosen AS (
                SELECT source FROM forecasts
                WHERE model_id = :model_id AND ts >= :start_ts AND ts < :end_ts
                ORDER BY (source = 'live') DESC
                LIMIT 1
            )
            SELECT f.ts, f.source, f.issued_at, f.yhat_mw, f.q10_mw, f.q90_mw,
                   d.demand_met_mw AS actual_mw
            FROM forecasts f
            LEFT JOIN demand_raw d ON d.ts = f.ts
            WHERE f.model_id = :model_id
              AND f.ts >= :start_ts AND f.ts < :end_ts
              AND f.source = (SELECT source FROM chosen)
            ORDER BY f.ts
            """,
            {"model_id": model_id, **_day_bounds(day, day)},
        )

    def weather_day(self, day: date) -> list[dict]:
        return self._all(
            """
            SELECT ts, temp_c FROM weather_forecast
            WHERE ts >= :start_ts AND ts < :end_ts AND temp_c IS NOT NULL
            ORDER BY ts
            """,
            _day_bounds(day, day),
        )

    # ------------------------------------------------------------ accuracy windows
    def accuracy_window(self, model_id: str, start: date, end: date, peak: tuple[int, int]) -> dict:
        """Aggregate accuracy + interval coverage over [start, end]."""
        return self._one(
            SCORED_CTE
            + """
            SELECT COUNT(*) AS n,
                   AVG(ape) AS mape,
                   AVG(ape) FILTER (WHERE EXTRACT(HOUR FROM ts) >= :peak_start
                                      AND EXTRACT(HOUR FROM ts) < :peak_end) AS peak_mape,
                   AVG(err_mw) AS bias_mw,
                   COUNT(*) FILTER (WHERE q10_mw IS NOT NULL AND q90_mw IS NOT NULL) AS interval_n,
                   COUNT(*) FILTER (WHERE actual_mw BETWEEN q10_mw AND q90_mw) AS covered_n
            FROM scored
            """,
            {"model_id": model_id, "peak_start": peak[0], "peak_end": peak[1], **_day_bounds(start, end)},
        )

    def accuracy_daily(self, model_id: str, start: date, end: date, peak: tuple[int, int]) -> list[dict]:
        return self._all(
            SCORED_CTE
            + """
            SELECT ts::date AS day, COUNT(*) AS n,
                   AVG(ape) AS mape,
                   AVG(ape) FILTER (WHERE EXTRACT(HOUR FROM ts) >= :peak_start
                                      AND EXTRACT(HOUR FROM ts) < :peak_end) AS peak_mape,
                   AVG(err_mw) AS bias_mw
            FROM scored
            GROUP BY ts::date
            ORDER BY day
            """,
            {"model_id": model_id, "peak_start": peak[0], "peak_end": peak[1], **_day_bounds(start, end)},
        )

    def bias_heatmap(self, model_id: str, start: date, end: date) -> list[dict]:
        return self._all(
            SCORED_CTE
            + """
            SELECT EXTRACT(ISODOW FROM ts)::int - 1 AS dow,
                   EXTRACT(HOUR FROM ts)::int AS hour,
                   COUNT(*) AS n,
                   AVG(err_mw) AS bias_mw
            FROM scored
            GROUP BY dow, hour
            ORDER BY dow, hour
            """,
            {"model_id": model_id, **_day_bounds(start, end)},
        )

    # ------------------------------------------------------------ data freshness
    def data_health(self) -> dict:
        return self._one(
            """
            WITH bounds AS (
                SELECT MIN(ts)::date AS first_day, MAX(ts)::date AS last_day,
                       MAX(ingested_at) AS last_ingested_at, COUNT(*) AS demand_rows
                FROM demand_raw
            )
            SELECT b.last_day AS last_actual_day,
                   b.last_ingested_at,
                   b.demand_rows,
                   (SELECT COUNT(*)
                      FROM generate_series(b.first_day, b.last_day, INTERVAL '1 day') AS g(day)
                     WHERE NOT EXISTS (
                         SELECT 1 FROM demand_raw d
                         WHERE d.ts >= g.day AND d.ts < g.day + INTERVAL '1 day'
                     )) AS missing_days
            FROM bounds b
            """
        )
