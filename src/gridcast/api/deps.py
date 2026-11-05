import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from gridcast.api.repository import ForecastRepository

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


def create_read_only_engine() -> Engine:
    database_url = os.getenv("APP_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL must be set for the read-only API database."
        )

    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL with the psycopg driver."
        )
    return create_engine(url, pool_pre_ping=True)


def get_repository(request: Request) -> ForecastRepository:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Read-only database is not configured.",
        )
    return ForecastRepository(engine)
