from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Actual(Base):
    __tablename__ = "actuals"
    __table_args__ = (
        UniqueConstraint("timestamp", name="uq_actual_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    demand_met_mw: Mapped[float] = mapped_column(Float, nullable=False)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_model_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id",
            "forecast_issued_at",
            "target_timestamp",
            name="uq_forecast_run_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id"), nullable=False, index=True
    )
    forecast_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    target_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    predicted_demand_mw: Mapped[float] = mapped_column(Float, nullable=False)


class WeatherForecast(Base):
    __tablename__ = "weather_forecasts"
    __table_args__ = (
        UniqueConstraint("target_timestamp", name="uq_weather_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, index=True
    )
    temperature_2m: Mapped[float | None] = mapped_column(Float)
    relative_humidity_2m: Mapped[float | None] = mapped_column(Float)
    apparent_temperature: Mapped[float | None] = mapped_column(Float)
    dew_point_2m: Mapped[float | None] = mapped_column(Float)