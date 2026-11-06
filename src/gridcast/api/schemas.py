"""Pydantic response models: the contract between the API and the dashboard JS."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Source = Literal["backtest", "live"]
Status = Literal["green", "yellow", "red", "neutral"]


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    service: str
    version: str


class ModelVersion(BaseModel):
    model_id: str
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


# ---------------------------------------------------------------- KPI tiles
class Kpi(BaseModel):
    value: float | None
    status: Status = "neutral"
    note: str = ""


class SummaryResponse(BaseModel):
    model_id: str
    date: date
    source: Source | None
    peak_mw: Kpi
    peak_time: str | None
    max_temp_c: Kpi
    mape_7d: Kpi
    coverage_7d: Kpi
    data_lag_days: Kpi


# ---------------------------------------------------------------- charts
class DayPoint(BaseModel):
    ts: datetime
    yhat_mw: float
    q10_mw: float | None
    q90_mw: float | None
    actual_mw: float | None
    temp_c: float | None


class DayResponse(BaseModel):
    model_id: str
    date: date
    source: Source
    issued_at: datetime
    points: list[DayPoint]


class DailyAccuracy(BaseModel):
    day: date
    n: int
    mape: float | None
    peak_mape: float | None
    bias_mw: float | None


class DailyAccuracyResponse(BaseModel):
    model_id: str
    start: date
    end: date
    items: list[DailyAccuracy]


class HeatmapCell(BaseModel):
    dow: int  # Monday = 0
    hour: int
    n: int
    bias_mw: float | None


class HeatmapResponse(BaseModel):
    model_id: str
    start: date
    end: date
    items: list[HeatmapCell]
