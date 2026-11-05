from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ForecastSource = Literal["backtest", "live"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    service: str
    version: str


class ModelVersion(BaseModel):
    model_id: str
    cv_source: Literal["backtest"] = "backtest"
    trained_at: datetime
    algo: str
    feature_set: str | None
    train_start: date | None
    train_end: date | None
    cv_mape: float | None
    cv_peak_mape: float | None
    cv_mase: float | None
    is_champion: bool


class ModelsResponse(BaseModel):
    items: list[ModelVersion]


class ForecastPoint(BaseModel):
    ts: datetime
    yhat_mw: float
    q10_mw: float | None
    q90_mw: float | None


class ForecastResponse(BaseModel):
    model_id: str
    source: ForecastSource
    date: date
    issued_at: datetime
    points: list[ForecastPoint]


class WeatherPoint(BaseModel):
    ts: datetime
    temp_c: float


class WeatherResponse(BaseModel):
    date: date
    max_temp_c: float | None
    points: list[WeatherPoint]


class ActualPoint(BaseModel):
    ts: datetime
    actual_mw: float


class ActualsResponse(BaseModel):
    start: date
    end: date
    points: list[ActualPoint]


class DailyAccuracy(BaseModel):
    day: date
    model_id: str
    source: ForecastSource
    n: int
    mape: float | None
    mae_mw: float
    bias_mw: float
    peak_mape: float | None


class DailyAccuracyResponse(BaseModel):
    items: list[DailyAccuracy]


class HeatmapCell(BaseModel):
    dow: int
    hour: int
    model_id: str
    source: ForecastSource
    n: int
    bias_mw: float
    mape: float | None


class HeatmapResponse(BaseModel):
    items: list[HeatmapCell]


class WorstDay(BaseModel):
    day: date
    model_id: str
    source: ForecastSource
    n: int
    mape: float | None
    bias_mw: float


class WorstDaysResponse(BaseModel):
    items: list[WorstDay]


class CoverageItem(BaseModel):
    model_id: str
    source: ForecastSource
    total: int
    covered: int
    share: float | None


class CoverageResponse(BaseModel):
    items: list[CoverageItem]


class DataHealthResponse(BaseModel):
    last_ingested_at: datetime | None
    missing_days: list[date]
    demand_rows: int
    weather_rows: int
    forecast_rows: int
    forecast_sources: list[ForecastSource] = ["live", "backtest"]
