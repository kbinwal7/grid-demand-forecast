"""JSON API. Every endpoint is read-only and maps to one part of the dashboard.

    GET /health                   liveness check
    GET /v1/models                model selector
    GET /v1/summary               KPI tiles
    GET /v1/day                   hero chart: forecast vs actual + temperature
    GET /v1/accuracy/daily        daily MAPE trend chart
    GET /v1/accuracy/heatmap      bias by weekday x hour chart

`model_id` defaults to the champion; `date` defaults to the latest forecast day.
"""

from datetime import date as Date
from datetime import datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from gridcast.api import kpis
from gridcast.api.deps import get_repository
from gridcast.api.repository import ForecastRepository
from gridcast.api.schemas import (
    DailyAccuracyResponse,
    DayResponse,
    HealthResponse,
    HeatmapResponse,
    Kpi,
    ModelsResponse,
    SummaryResponse,
)

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")
Repo =Annotated[ForecastRepository, Depends(get_repository)]
Days = Annotated[int, Query(ge=1, le=92, description="Window length ending at `date`")]


def _resolve(repo: ForecastRepository, model_id: str | None, day: Date | None) -> tuple[dict, list[dict], Date]:
    """Pick the model (default: champion) and day (default: its latest forecast day)."""
    models = repo.models()
    if not models:
        raise HTTPException(404, "No models are registered yet. Run scripts/seed_demo.py or the pipeline.")
    if model_id is None:
        model = next((m for m in models if m["is_champion"]), models[0])
    else:
        model = next((m for m in models if m["model_id"] == model_id), None)
        if model is None:
            raise HTTPException(404, f"Unknown model_id {model_id!r}.")
    day = day or repo.latest_forecast_date(model["model_id"])
    if day is None:
        raise HTTPException(404, f"Model {model['model_id']!r} has no forecasts.")
    return model, models, day


def _window_end(repo: ForecastRepository, model_id: str, day: Date) -> Date:
    """Accuracy windows end at the last day with actuals (a future forecast day has none)."""
    return repo.last_scored_date(model_id, day) or day


@router.get("/health", response_model=HealthResponse)
def health() -> dict:
    return {"status": "healthy", "service": "GridCast API", "version": "0.1.0"}


@router.get("/v1/models", response_model=ModelsResponse)
def models(repo: Repo) -> dict:
    return {"items": repo.models()}


@router.get("/v1/summary", response_model=SummaryResponse)
def summary(repo: Repo, model_id: str | None = None, date: Date | None = None) -> dict:
    model, models, day = _resolve(repo, model_id, date)
    mid = model["model_id"]

    # Forecast day: expected peak.
    points = repo.forecast_day(mid, day)
    peak = max(points, key=lambda p: p["yhat_mw"]) if points else None

    # Weather: max temperature.
    temps = [w["temp_c"] for w in repo.weather_day(day)]

    # Accuracy over the last KPI_WINDOW_DAYS scored days.
    end = _window_end(repo, mid, day)
    start = end - timedelta(days=kpis.KPI_WINDOW_DAYS - 1)
    acc = repo.accuracy_window(mid, start, end, kpis.PEAK_HOURS)
    champion = next((m for m in models if m["is_champion"]), None)
    baseline = next((m for m in models if m["model_id"] == kpis.BASELINE_MODEL_ID), None)
    mape = acc.get("mape")
    interval_n = acc.get("interval_n") or 0
    coverage = 100 * acc["covered_n"] / interval_n if interval_n else None

    # Data freshness: how many days the latest actual lags today (IST).
    health = repo.data_health()
    last_actual = health.get("last_actual_day")
    lag = (datetime.now(IST).date() - last_actual).days if last_actual else None
    missing = health.get("missing_days") or 0
    window = f"{start:%d %b} – {end:%d %b}"

    return {
        "model_id": mid,
        "date": day,
        "source": points[0]["source"] if points else None,
        "peak_mw": Kpi(value=peak["yhat_mw"] if peak else None, note=f"forecast for {day:%d %b %Y}"),
        "peak_time": peak["ts"].strftime("%H:%M") if peak else None,
        "max_temp_c": Kpi(value=max(temps) if temps else None, note="weather forecast, Delhi"),
        "mape_7d": Kpi(
            value=mape,
            status=kpis.mape_status(mape, champion and champion["cv_mape"], baseline and baseline["cv_mape"]),
            note=f"{window} · baseline {baseline['cv_mape']:.2f}%" if baseline and baseline["cv_mape"] else window,
        ),
        "coverage_7d": Kpi(
            value=coverage,
            status=kpis.coverage_status(coverage, interval_n),
            note=f"{window} · target 80%",
        ),
        "data_lag_days": Kpi(
            value=lag,
            status=kpis.freshness_status(lag, missing),
            note=f"last actual {last_actual:%d %b}" + (f" · {missing} missing days" if missing else "")
            if last_actual
            else "no actuals loaded",
        ),
    }


@router.get("/v1/day", response_model=DayResponse)
def day_detail(repo: Repo, model_id: str | None = None, date: Date | None = None) -> dict:
    model, _, day = _resolve(repo, model_id, date)
    points = repo.forecast_day(model["model_id"], day)
    if not points:
        raise HTTPException(404, f"No forecast for {model['model_id']} on {day}.")
    temps = {w["ts"]: w["temp_c"] for w in repo.weather_day(day)}
    return {
        "model_id": model["model_id"],
        "date": day,
        "source": points[0]["source"],
        "issued_at": points[0]["issued_at"],
        "points": [{**p, "temp_c": temps.get(p["ts"])} for p in points],
    }


@router.get("/v1/accuracy/daily", response_model=DailyAccuracyResponse)
def accuracy_daily(repo: Repo, model_id: str | None = None, date: Date | None = None, days: Days = 30) -> dict:
    model, _, day = _resolve(repo, model_id, date)
    end = _window_end(repo, model["model_id"], day)
    start = end - timedelta(days=days - 1)
    items = repo.accuracy_daily(model["model_id"], start, end, kpis.PEAK_HOURS)
    return {"model_id": model["model_id"], "start": start, "end": end, "items": items}


@router.get("/v1/accuracy/heatmap", response_model=HeatmapResponse)
def accuracy_heatmap(repo: Repo, model_id: str | None = None, date: Date | None = None, days: Days = 30) -> dict:
    model, _, day = _resolve(repo, model_id, date)
    end = _window_end(repo, model["model_id"], day)
    start = end - timedelta(days=days - 1)
    items = repo.bias_heatmap(model["model_id"], start, end)
    return {"model_id": model["model_id"], "start": start, "end": end, "items": items}
