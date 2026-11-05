import ipaddress
import os
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from gridcast.api.deps import get_repository
from gridcast.api.repository import ForecastRepository
from gridcast.api.schemas import (
    ActualsResponse,
    CoverageResponse,
    DailyAccuracyResponse,
    DataHealthResponse,
    ForecastResponse,
    ForecastSource,
    HealthResponse,
    HeatmapResponse,
    ModelsResponse,
    WeatherResponse,
    WorstDaysResponse,
)

router = APIRouter()
RATE_LIMIT = os.getenv("RATE_LIMIT", "60/minute")


def proxy_aware_key(request: Request) -> str:
    """Key requests by the proxy-forwarded client IP, falling back to the socket."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        candidate = forwarded_for.split(",", maxsplit=1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=proxy_aware_key, default_limits=[RATE_LIMIT])
RepositoryDep = Annotated[ForecastRepository, Depends(get_repository)]


def _validate_range(start: date | None, end: date | None) -> None:
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=422,
            detail="Both start and end dates must be supplied together.",
        )
    if start is not None and end is not None:
        if end < start:
            raise HTTPException(
                status_code=422, detail="end must be on or after start."
            )
        if (end - start).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range must not exceed 92 days.",
            )


@router.get("/health", response_model=HealthResponse)
@limiter.limit(RATE_LIMIT)
def health(request: Request) -> dict:
    return {"status": "healthy", "service": "GridCast API", "version": "0.1.0"}


@router.get("/v1/models", response_model=ModelsResponse)
@limiter.limit(RATE_LIMIT)
def models(request: Request, repository: RepositoryDep) -> dict:
    return {"items": repository.models()}


@router.get("/v1/forecast", response_model=ForecastResponse)
@limiter.limit(RATE_LIMIT)
def forecast(
    request: Request,
    repository: RepositoryDep,
    date: date | None = None,
    model_id: str | None = None,
    source: ForecastSource | None = None,
) -> dict:
    target_date = date or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    rows = repository.forecast(target_date, model_id, source)
    if not rows:
        raise HTTPException(
            status_code=404, detail="No forecast is available for that date."
        )
    first = rows[0]
    return {
        "model_id": first["model_id"],
        "source": first["source"],
        "date": target_date,
        "issued_at": first["issued_at"],
        "points": [
            {
                "ts": row["ts"],
                "yhat_mw": row["yhat_mw"],
                "q10_mw": row["q10_mw"],
                "q90_mw": row["q90_mw"],
            }
            for row in rows
        ],
    }


@router.get("/v1/actuals", response_model=ActualsResponse)
@limiter.limit(RATE_LIMIT)
def actuals(
    request: Request,
    start: date,
    end: date,
    repository: RepositoryDep,
) -> dict:
    _validate_range(start, end)
    return {"start": start, "end": end, "points": repository.actuals(start, end)}


@router.get("/v1/weather", response_model=WeatherResponse)
@limiter.limit(RATE_LIMIT)
def weather(
    request: Request,
    repository: RepositoryDep,
    date: date | None = None,
) -> dict:
    target_date = date or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    return repository.weather_summary(target_date)


@router.get("/v1/accuracy/daily", response_model=DailyAccuracyResponse)
@limiter.limit(RATE_LIMIT)
def accuracy_daily(
    request: Request,
    repository: RepositoryDep,
    start: date | None = None,
    end: date | None = None,
    model_id: str | None = None,
    source: ForecastSource | None = None,
) -> dict:
    _validate_range(start, end)
    return {"items": repository.accuracy_daily(start, end, model_id, source)}


@router.get("/v1/accuracy/heatmap", response_model=HeatmapResponse)
@limiter.limit(RATE_LIMIT)
def accuracy_heatmap(
    request: Request,
    repository: RepositoryDep,
    start: date | None = None,
    end: date | None = None,
    model_id: str | None = None,
    source: ForecastSource | None = None,
) -> dict:
    _validate_range(start, end)
    return {"items": repository.accuracy_heatmap(start, end, model_id, source)}


@router.get("/v1/accuracy/worst-days", response_model=WorstDaysResponse)
@limiter.limit(RATE_LIMIT)
def accuracy_worst_days(
    request: Request,
    repository: RepositoryDep,
    n: Annotated[int, Query(ge=1, le=100)] = 10,
    start: date | None = None,
    end: date | None = None,
    model_id: str | None = None,
    source: ForecastSource | None = None,
) -> dict:
    _validate_range(start, end)
    return {"items": repository.worst_days(n, start, end, model_id, source)}


@router.get("/v1/accuracy/coverage", response_model=CoverageResponse)
@limiter.limit(RATE_LIMIT)
def accuracy_coverage(
    request: Request,
    repository: RepositoryDep,
    start: date | None = None,
    end: date | None = None,
    model_id: str | None = None,
    source: ForecastSource | None = None,
) -> dict:
    _validate_range(start, end)
    return {"items": repository.coverage(start, end, model_id, source)}


@router.get("/v1/data-health", response_model=DataHealthResponse)
@limiter.limit(RATE_LIMIT)
def data_health(request: Request, repository: RepositoryDep) -> dict:
    return repository.data_health()
