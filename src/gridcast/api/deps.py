"""FastAPI dependencies: the read-only database engine and repository."""

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
    """Engine for DATABASE_URL, which should point at the read-only app role."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set for the API.")
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError("DATABASE_URL must be a postgresql:// URL.")
    return create_engine(url, pool_pre_ping=True)


def get_repository(request: Request) -> ForecastRepository:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Database is not configured: set DATABASE_URL in .env and restart.",
        )
    return ForecastRepository(engine)
