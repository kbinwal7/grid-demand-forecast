from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from gridcast.api.deps import create_read_only_engine, get_repository
from gridcast.api.main import create_app

DAY = date(2026, 1, 2)


def _points(day: date, with_actual: bool):
    start = datetime.combine(day, datetime.min.time())
    return [
        {
            "ts": start + timedelta(minutes=15 * i),
            "source": "backtest",
            "issued_at": start - timedelta(hours=14),
            "yhat_mw": 1000.0 + i,
            "q10_mw": 950.0 + i,
            "q90_mw": 1050.0 + i,
            "actual_mw": 1001.0 + i if with_actual else None,
        }
        for i in range(96)
    ]


class FakeRepository:
    def models(self):
        base = {"trained_at": datetime(2026, 1, 1), "feature_set": None, "train_start": None,
                "train_end": None, "cv_peak_mape": None, "cv_mase": None}
        return [
            {**base, "model_id": "lgbm", "algo": "lightgbm", "cv_mape": 2.0, "is_champion": True},
            {**base, "model_id": "blend_1d_1w", "algo": "blend", "cv_mape": 4.0, "is_champion": False},
        ]

    def latest_forecast_date(self, model_id):
        return DAY

    def last_scored_date(self, model_id, on_or_before):
        return min(on_or_before, DAY)

    def forecast_day(self, model_id, day):
        return _points(day, with_actual=True) if day <= DAY else []

    def weather_day(self, day):
        return [{"ts": datetime.combine(day, datetime.min.time()) + timedelta(hours=14), "temp_c": 31.5}]

    def accuracy_window(self, model_id, start, end, peak):
        return {"n": 672, "mape": 2.1, "peak_mape": 2.5, "bias_mw": 3.0, "interval_n": 672, "covered_n": 540}

    def accuracy_daily(self, model_id, start, end, peak):
        return [{"day": end, "n": 96, "mape": 2.1, "peak_mape": 2.5, "bias_mw": 3.0}]

    def bias_heatmap(self, model_id, start, end):
        return [{"dow": 0, "hour": 18, "n": 4, "bias_mw": 10.0}]

    def data_health(self):
        return {"last_actual_day": DAY, "last_ingested_at": None, "demand_rows": 96, "missing_days": 0}


def client() -> TestClient:
    app = create_app(initialize_engine=False)
    app.dependency_overrides[get_repository] = FakeRepository
    return TestClient(app)


def test_models_lists_champion_first():
    items = client().get("/v1/models").json()["items"]
    assert items[0]["model_id"] == "lgbm" and items[0]["is_champion"]


def test_summary_defaults_to_champion_and_latest_day():
    body = client().get("/v1/summary").json()
    assert body["model_id"] == "lgbm"
    assert body["date"] == DAY.isoformat()
    assert body["peak_mw"]["value"] == 1095.0
    assert body["peak_time"] == "23:45"
    assert body["max_temp_c"]["value"] == 31.5
    assert body["mape_7d"]["status"] == "green"  # 2.1 <= min(1.15 * 2.0, 4.0)
    assert round(body["coverage_7d"]["value"], 2) == round(100 * 540 / 672, 2)
    assert body["coverage_7d"]["status"] == "green"  # 540/672 = 80.4%, inside 75-85
    assert body["data_lag_days"]["value"] is not None


def test_day_merges_actuals_and_temperature():
    body = client().get("/v1/day", params={"date": DAY.isoformat()}).json()
    assert body["source"] == "backtest"
    assert len(body["points"]) == 96
    assert body["points"][56]["temp_c"] == 31.5  # 14:00
    assert body["points"][0]["temp_c"] is None


def test_day_without_forecast_is_404_with_error_body():
    response = client().get("/v1/day", params={"date": "2030-01-01"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_model_is_404():
    response = client().get("/v1/summary", params={"model_id": "nope"})
    assert response.status_code == 404


def test_accuracy_endpoints_window_ends_at_last_scored_day():
    c = client()
    daily = c.get("/v1/accuracy/daily", params={"date": "2026-01-05", "days": 7}).json()
    assert daily["end"] == DAY.isoformat()
    assert daily["start"] == (DAY - timedelta(days=6)).isoformat()
    heat = c.get("/v1/accuracy/heatmap").json()
    assert heat["items"][0] == {"dow": 0, "hour": 18, "n": 4, "bias_mw": 10.0}


def test_validation_errors_use_consistent_body():
    response = client().get("/v1/accuracy/daily", params={"days": 500})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_missing_database_returns_503():
    with TestClient(create_app(initialize_engine=False)) as c:
        response = c.get("/v1/models")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


def test_api_is_read_only():
    paths = client().get("/openapi.json").json()["paths"]
    assert all(set(item) == {"get"} for item in paths.values())


def test_dashboard_page_and_static_assets():
    c = client()
    page = c.get("/")
    assert page.status_code == 200
    for element_id in ("kpi-peak", "kpi-mape", "chart-day", "chart-daily", "chart-bias"):
        assert f'id="{element_id}"' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    for path in ("/static/css/app.css", "/static/js/dashboard.js", "/static/js/api.js",
                 "/static/vendor/plotly-cartesian-2.35.2.min.js"):
        assert c.get(path).status_code == 200, path
    assert c.get("/health").json()["status"] == "healthy"


def test_read_only_engine_normalizes_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://reader:pw@localhost:5432/gridcast")
    captured = {}
    monkeypatch.setattr(
        "gridcast.api.deps.create_engine",
        lambda url, **kw: captured.update(url=url, kw=kw) or "engine",
    )
    assert create_read_only_engine() == "engine"
    assert captured["url"].drivername == "postgresql+psycopg"
    assert captured["kw"] == {"pool_pre_ping": True}
