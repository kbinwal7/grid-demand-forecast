from datetime import date, datetime, time, timedelta

from sqlalchemy import Engine, text


def _records(rows) -> list[dict]:
    return [dict(row._mapping) for row in rows]


class ForecastRepository:
    """Read-only SQL access to the application schema."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def _all(self, statement: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as connection:
            return _records(connection.execute(text(statement), params or {}).all())

    def models(self) -> list[dict]:
        return self._all(
            """
            SELECT model_id, trained_at, algo, feature_set, train_start, train_end,
                   cv_mape, cv_peak_mape, cv_mase, is_champion
            FROM model_versions
            ORDER BY is_champion DESC, trained_at DESC, model_id
            """
        )

    def forecast(
        self, target_date: date, model_id: str | None, source: str | None
    ) -> list[dict]:
        return self._all(
            """
            WITH chosen_model AS (
                SELECT COALESCE(
                    :model_id,
                    (
                        SELECT model_id FROM model_versions
                        WHERE is_champion
                        LIMIT 1
                    )
                ) AS model_id
            ),
            chosen_source AS (
                SELECT f.source
                FROM forecasts f
                CROSS JOIN chosen_model m
                WHERE f.ts >= :day_start
                  AND f.ts < :day_end
                  AND f.model_id = m.model_id
                  AND (:source IS NULL OR f.source = :source)
                ORDER BY CASE f.source WHEN 'live' THEN 0 ELSE 1 END
                LIMIT 1
            )
            SELECT model_id, source, ts, issued_at, yhat_mw, q10_mw, q90_mw
            FROM forecasts
            WHERE ts >= :day_start
              AND ts < :day_end
              AND model_id = (SELECT model_id FROM chosen_model)
              AND source = (SELECT source FROM chosen_source)
            ORDER BY ts
            """,
            {
                "day_start": datetime.combine(target_date, time.min),
                "day_end": datetime.combine(target_date + timedelta(days=1), time.min),
                "model_id": model_id,
                "source": source,
            },
        )

    def actuals(self, start: date, end: date) -> list[dict]:
        return self._all(
            """
            SELECT ts, demand_met_mw AS actual_mw
            FROM demand_raw
            WHERE ts >= :start_ts AND ts < :end_ts
            ORDER BY ts
            """,
            {
                "start_ts": datetime.combine(start, time.min),
                "end_ts": datetime.combine(end + timedelta(days=1), time.min),
            },
        )

    def weather_summary(self, target_date: date) -> dict:
        rows = self._all(
            """
            SELECT MAX(temp_c) AS max_temp_c
            FROM weather_forecast
            WHERE ts >= :day_start AND ts < :day_end
            """,
            {
                "day_start": datetime.combine(target_date, time.min),
                "day_end": datetime.combine(target_date + timedelta(days=1), time.min),
            },
        )
        return {"date": target_date, **rows[0]}

    @staticmethod
    def _filters(start: date | None, end: date | None) -> dict:
        return {
            "start_ts": datetime.combine(start, time.min) if start else None,
            "end_ts": datetime.combine(end + timedelta(days=1), time.min)
            if end
            else None,
        }

    def accuracy_daily(
        self,
        start: date | None,
        end: date | None,
        model_id: str | None,
        source: str | None,
    ) -> list[dict]:
        return self._all(
            """
            SELECT ts::date AS day, model_id, source, COUNT(*) AS n,
                   AVG(ape) AS mape,
                   AVG(ABS(err_mw)) AS mae_mw,
                   AVG(err_mw) AS bias_mw,
                   AVG(ape) FILTER (
                       WHERE EXTRACT(HOUR FROM ts) >= 18
                         AND EXTRACT(HOUR FROM ts) < 22
                   ) AS peak_mape
            FROM forecast_vs_actual
            WHERE (:start_ts IS NULL OR ts >= :start_ts)
              AND (:end_ts IS NULL OR ts < :end_ts)
              AND (:model_id IS NULL OR model_id = :model_id)
              AND (:source IS NULL OR source = :source)
            GROUP BY ts::date, model_id, source
            ORDER BY day, model_id, source
            """,
            {**self._filters(start, end), "model_id": model_id, "source": source},
        )

    def accuracy_heatmap(
        self,
        start: date | None,
        end: date | None,
        model_id: str | None,
        source: str | None,
    ) -> list[dict]:
        return self._all(
            """
            SELECT EXTRACT(ISODOW FROM ts)::integer - 1 AS dow,
                   EXTRACT(HOUR FROM ts)::integer AS hour,
                   model_id, source, COUNT(*) AS n,
                   AVG(err_mw) AS bias_mw,
                   AVG(ape) AS mape
            FROM forecast_vs_actual
            WHERE (:start_ts IS NULL OR ts >= :start_ts)
              AND (:end_ts IS NULL OR ts < :end_ts)
              AND (:model_id IS NULL OR model_id = :model_id)
              AND (:source IS NULL OR source = :source)
            GROUP BY dow, hour, model_id, source
            ORDER BY model_id, source, dow, hour
            """,
            {**self._filters(start, end), "model_id": model_id, "source": source},
        )

    def worst_days(
        self,
        n: int,
        start: date | None,
        end: date | None,
        model_id: str | None,
        source: str | None,
    ) -> list[dict]:
        return self._all(
            """
            SELECT ts::date AS day, model_id, source, COUNT(*) AS n,
                   AVG(ape) AS mape, AVG(err_mw) AS bias_mw
            FROM forecast_vs_actual
            WHERE (:start_ts IS NULL OR ts >= :start_ts)
              AND (:end_ts IS NULL OR ts < :end_ts)
              AND (:model_id IS NULL OR model_id = :model_id)
              AND (:source IS NULL OR source = :source)
            GROUP BY ts::date, model_id, source
            ORDER BY mape DESC NULLS LAST, day DESC
            LIMIT :row_limit
            """,
            {
                **self._filters(start, end),
                "model_id": model_id,
                "source": source,
                "row_limit": n,
            },
        )

    def coverage(
        self,
        start: date | None,
        end: date | None,
        model_id: str | None,
        source: str | None,
    ) -> list[dict]:
        return self._all(
            """
            SELECT model_id, source,
                   COUNT(*) FILTER (
                       WHERE q10_mw IS NOT NULL AND q90_mw IS NOT NULL
                   ) AS total,
                   COUNT(*) FILTER (
                       WHERE actual_mw BETWEEN q10_mw AND q90_mw
                   ) AS covered,
                   COUNT(*) FILTER (
                       WHERE actual_mw BETWEEN q10_mw AND q90_mw
                   )::double precision / NULLIF(COUNT(*) FILTER (
                       WHERE q10_mw IS NOT NULL AND q90_mw IS NOT NULL
                   ), 0) AS share
            FROM forecast_vs_actual
            WHERE (:start_ts IS NULL OR ts >= :start_ts)
              AND (:end_ts IS NULL OR ts < :end_ts)
              AND (:model_id IS NULL OR model_id = :model_id)
              AND (:source IS NULL OR source = :source)
            GROUP BY model_id, source
            ORDER BY model_id, source
            """,
            {**self._filters(start, end), "model_id": model_id, "source": source},
        )

    def data_health(self) -> dict:
        with self.engine.connect() as connection:
            demand = connection.execute(
                text(
                    """
                    SELECT COUNT(*) AS row_count, MIN(ts::date) AS first_day,
                           MAX(ts::date) AS last_day, MAX(ingested_at) AS last_ingested_at
                    FROM demand_raw
                    """
                )
            ).one()
            counts = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM weather_forecast) AS weather_rows,
                      (SELECT COUNT(*) FROM forecasts) AS forecast_rows
                    """
                )
            ).one()

            missing_days = []
            if demand.first_day is not None:
                present = set(
                    connection.execute(
                        text(
                            """
                            SELECT DISTINCT ts::date
                            FROM demand_raw
                            WHERE ts::date BETWEEN :first_day AND :last_day
                            """
                        ),
                        {"first_day": demand.first_day, "last_day": demand.last_day},
                    ).scalars()
                )
                current = demand.first_day
                while current <= demand.last_day:
                    if current not in present:
                        missing_days.append(current)
                    current += timedelta(days=1)

        return {
            "last_ingested_at": demand.last_ingested_at,
            "missing_days": missing_days,
            "demand_rows": demand.row_count,
            "weather_rows": counts.weather_rows,
            "forecast_rows": counts.forecast_rows,
        }
