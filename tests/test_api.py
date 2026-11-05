from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from starlette.requests import Request

from gridcast.api.deps import create_read_only_engine, get_repository
from gridcast.api.main import create_app
from gridcast.api.routes_api import proxy_aware_key


class FakeRepository:
    def __init__(self):
        self.calls = []

    def models(self):
        return [
            {
                "model_id": "blend_1d_1w",
                "trained_at": datetime(2026, 1, 1),
                "algo": "blend",
                "feature_set": "demand lags",
                "train_start": date(2024, 1, 1),
                "train_end": date(2025, 12, 31),
                "cv_mape": 1.2,
                "cv_peak_mape": 2.3,
                "cv_mase": 0.4,
                "is_champion": True,
            }
        ]

    def forecast(self, target_date, model_id, source):
        self.calls.append(("forecast", target_date, model_id, source))
        return [
            {
                "model_id": model_id or "blend_1d_1w",
                "source": source or "backtest",
                "ts": datetime(2026, 1, 1),
                "issued_at": datetime(2026, 1, 1),
                "yhat_mw": 123.0,
                "q10_mw": None,
                "q90_mw": None,
            }
        ]

    def actuals(self, start, end):
        self.calls.append(("actuals", start, end))
        return [{"ts": datetime(2026, 1, 1), "actual_mw": 120.0}]

    def weather_summary(self, target_date):
        return {
            "date": target_date,
            "max_temp_c": 34.0,
            "points": [
                {"ts": datetime(2026, 1, 1, 6), "temp_c": 22.0},
                {"ts": datetime(2026, 1, 1, 12), "temp_c": 34.0},
            ],
        }

    def accuracy_daily(self, start, end, model_id, source):
        return [
            {
                "day": date(2026, 1, 1),
                "model_id": model_id or "blend_1d_1w",
                "source": source or "backtest",
                "n": 96,
                "mape": 1.0,
                "mae_mw": 10.0,
                "bias_mw": 2.0,
                "peak_mape": 1.5,
            }
        ]

    def accuracy_heatmap(self, start, end, model_id, source):
        return [
            {
                "dow": 0,
                "hour": 18,
                "model_id": model_id or "blend_1d_1w",
                "source": source or "backtest",
                "n": 4,
                "bias_mw": 10.0,
                "mape": 2.0,
            }
        ]

    def worst_days(self, n, start, end, model_id, source):
        self.calls.append(("worst_days", n, start, end, model_id, source))
        return [
            {
                "day": date(2026, 1, 1),
                "model_id": model_id or "blend_1d_1w",
                "source": source or "backtest",
                "n": 96,
                "mape": 4.2,
                "bias_mw": -50.0,
            }
        ]

    def coverage(self, start, end, model_id, source):
        return [
            {
                "model_id": model_id or "blend_1d_1w",
                "source": source or "backtest",
                "total": 0,
                "covered": 0,
                "share": None,
            }
        ]

    def data_health(self):
        return {
            "last_ingested_at": datetime(2026, 1, 1),
            "missing_days": [date(2025, 12, 31)],
            "demand_rows": 100,
            "weather_rows": 50,
            "forecast_rows": 96,
        }


def api_client():
    repository = FakeRepository()
    app = create_app(initialize_engine=False)
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app), repository


def test_read_api_routes_return_explicit_response_shapes():
    client, repository = api_client()

    models = client.get("/v1/models")
    assert models.status_code == 200
    assert models.json()["items"][0]["is_champion"] is True
    assert models.json()["items"][0]["cv_source"] == "backtest"

    forecast = client.get(
        "/v1/forecast",
        params={"date": "2026-01-01", "source": "backtest"},
    )
    assert forecast.status_code == 200
    assert forecast.json()["source"] == "backtest"
    assert forecast.json()["points"][0]["yhat_mw"] == 123.0
    assert repository.calls[-1] == (
        "forecast",
        date(2026, 1, 1),
        None,
        "backtest",
    )

    actuals = client.get(
        "/v1/actuals",
        params={"start": "2026-01-01", "end": "2026-01-02"},
    )
    assert actuals.status_code == 200
    assert actuals.json()["points"][0]["actual_mw"] == 120.0

    assert client.get("/v1/accuracy/daily").json()["items"][0]["peak_mape"] == 1.5
    heatmap = client.get("/v1/accuracy/heatmap").json()["items"][0]
    assert (heatmap["dow"], heatmap["hour"], heatmap["source"]) == (
        0,
        18,
        "backtest",
    )
    assert client.get("/v1/accuracy/worst-days").json()["items"][0]["mape"] == 4.2
    assert client.get("/v1/accuracy/coverage").json()["items"][0]["share"] is None
    data_health = client.get("/v1/data-health").json()
    assert data_health["demand_rows"] == 100
    assert data_health["forecast_sources"] == ["live", "backtest"]
    weather = client.get("/v1/weather", params={"date": "2026-01-01"})
    assert weather.status_code == 200
    assert weather.json() == {
        "date": "2026-01-01",
        "max_temp_c": 34.0,
        "points": [
            {"ts": "2026-01-01T06:00:00", "temp_c": 22.0},
            {"ts": "2026-01-01T12:00:00", "temp_c": 34.0},
        ],
    }
    assert client.get("/health").json()["status"] == "healthy"
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200

    paths = client.get("/openapi.json").json()["paths"]
    assert all(
        not {"post", "put", "patch", "delete"}.intersection(path_item)
        for path_item in paths.values()
    )


def test_forecast_defaults_to_today_in_ist_and_champion():
    client, repository = api_client()

    response = client.get("/v1/forecast")

    expected_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert response.status_code == 200
    assert response.json()["date"] == expected_date.isoformat()
    assert repository.calls[-1] == ("forecast", expected_date, None, None)


def test_date_range_validation_has_consistent_error_body():
    client, _ = api_client()

    incomplete = client.get("/v1/accuracy/daily", params={"start": "2026-01-01"})
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "validation_error"

    too_long = client.get(
        "/v1/actuals",
        params={"start": "2026-01-01", "end": "2026-04-03"},
    )
    assert too_long.status_code == 422
    assert "92 days" in too_long.json()["error"]["message"]

    invalid_source = client.get("/v1/forecast", params={"source": "not-a-source"})
    assert invalid_source.status_code == 422
    assert invalid_source.json()["error"]["code"] == "validation_error"


def test_repository_dependency_returns_consistent_not_found_error():
    client, _ = api_client()
    app = client.app
    app.dependency_overrides[get_repository] = lambda: type(
        "EmptyRepository",
        (),
        {"forecast": lambda self, target_date, model_id, source: []},
    )()

    response = client.get("/v1/forecast", params={"date": "2026-01-01"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "No forecast is available for that date.",
        }
    }


def test_missing_database_has_consistent_service_unavailable_error():
    app = create_app(initialize_engine=False)
    with TestClient(app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Read-only database is not configured.",
        }
    }


def test_read_only_engine_uses_database_url_with_pool_pre_ping(monkeypatch):
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://gridcast_reader:test-only-password@localhost:5432/gridcast",
    )
    captured = {}
    engine = object()

    def fake_create_engine(url, **options):
        captured["url"] = url
        captured["options"] = options
        return engine

    monkeypatch.setattr("gridcast.api.deps.create_engine", fake_create_engine)

    assert create_read_only_engine() is engine
    assert captured["url"].drivername == "postgresql+psycopg"
    assert captured["options"] == {"pool_pre_ping": True}


def test_app_creates_and_disposes_configured_engine_once(monkeypatch):
    class FakeEngine:
        dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    engine = FakeEngine()
    factory_calls = 0

    def create_engine():
        nonlocal factory_calls
        factory_calls += 1
        return engine

    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://reader:password@localhost/gridcast",
    )
    monkeypatch.setattr("gridcast.api.main.create_read_only_engine", create_engine)
    app = create_app()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200

    assert factory_calls == 1
    assert engine.dispose_calls == 1


def test_accuracy_range_is_inclusive_and_capped_at_92_days():
    client, _ = api_client()

    assert (
        client.get(
            "/v1/accuracy/daily",
            params={"start": "2026-01-01", "end": "2026-04-02"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/v1/accuracy/daily",
            params={"start": "2026-01-01", "end": "2026-04-03"},
        ).status_code
        == 422
    )


def test_dashboard_is_a_single_page_with_all_tabs_and_stat_tiles():
    app = create_app(initialize_engine=False)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        for tab_id in ("forecast", "accuracy", "failures", "models", "data-health"):
            assert f'id="{tab_id}"' in response.text
        assert 'id="dashboard-stat-tiles"' in response.text
        assert "Weather data by Open-Meteo.com" in response.text
        for path in ("/forecast", "/accuracy", "/failures", "/models", "/data-health"):
            assert client.get(path).status_code == 404


def test_dashboard_security_and_static_cache_headers():
    app = create_app(initialize_engine=False)

    with TestClient(app) as client:
        page = client.get("/")
        static = client.get("/static/css/app.css")

    assert page.status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "style-src 'self'" in page.headers["content-security-policy"]
    assert "'unsafe-inline'" not in page.headers["content-security-policy"]
    assert "nonce-" in page.headers["content-security-policy"]
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert "img-src 'self' data:" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert static.status_code == 200
    assert static.headers["cache-control"] == "public, max-age=3600"


def test_trusted_hosts_include_configured_host_and_render_wildcard(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "example.com")
    app = create_app(initialize_engine=False)

    with TestClient(app) as client:
        rejected = client.get("/")
        allowed_render = client.get("/", headers={"host": "gridcast.onrender.com"})
        allowed_configured = client.get("/", headers={"host": "example.com"})

    assert rejected.status_code == 400
    assert allowed_render.status_code == 200
    assert allowed_configured.status_code == 200


def test_json_api_responses_are_gzipped_and_support_etag_revalidation():
    client, repository = api_client()
    model = repository.models()[0]
    repository.models = lambda: [
        {**model, "model_id": f"fixture_model_{index}"}
        for index in range(8)
    ]

    response = client.get("/v1/models", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    etag = response.headers["etag"]

    cached = client.get(
        "/v1/models",
        headers={"accept-encoding": "gzip", "if-none-match": etag},
    )
    assert cached.status_code == 304
    assert cached.headers["etag"] == etag


def test_proxy_aware_rate_limit_key_uses_forwarded_client_ip_and_falls_back():
    base_scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/models",
        "raw_path": b"/v1/models",
        "query_string": b"",
        "headers": [],
        "client": ("10.0.0.8", 8000),
        "server": ("gridcast", 8000),
    }

    forwarded = dict(base_scope, headers=[(b"x-forwarded-for", b"203.0.113.42, 10.0.0.2")])
    invalid = dict(base_scope, headers=[(b"x-forwarded-for", b"not-an-ip")])

    assert proxy_aware_key(Request(forwarded)) == "203.0.113.42"
    assert proxy_aware_key(Request(invalid)) == "10.0.0.8"
