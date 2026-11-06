"""Write-capable database connection used by scripts and pipeline jobs (never the web app)."""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _database_url() -> str | URL:
    if url := os.getenv("ADMIN_DATABASE_URL"):
        parsed_url = make_url(url)
        if parsed_url.drivername == "postgresql":
            parsed_url = parsed_url.set(drivername="postgresql+psycopg")
        return parsed_url
    if os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "ADMIN_DATABASE_URL is required for database writes; "
            "DATABASE_URL is reserved for the read-only application role."
        )

    return URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "gridcast"),
    )


engine: Engine = create_engine(_database_url(), pool_pre_ping=True, echo=False)


def get_engine() -> Engine:
    return engine


COLS = [
    "frequency_hz",
    "demand_met_mw",
    "nuclear_mw",
    "wind_mw",
    "solar_mw",
    "hydro_mw",
    "gas_mw",
    "thermal_mw",
    "others_mw",
    "net_demand_met_mw",
    "total_generation_mw",
    "net_exchange_mw",
]


def upsert_raw(df: pd.DataFrame, engine=None) -> int:
    """Upsert a datetime-indexed frame with the columns in COLS."""

    engine = engine or get_engine()

    if df.index.name != "datetime":
        raise ValueError(
            "Expected DataFrame index to be named 'datetime'. "
            f"Got: {df.index.name!r}"
        )

    out = df.rename(
        columns={"net_transnational_exchange_mw": "net_exchange_mw"}
    )

    missing = [col for col in COLS if col not in out.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    out = out[COLS].reset_index().rename(columns={"datetime": "ts"})
    records = out.astype(object).where(out.notna(), None).to_dict("records")

    if not records:
        return 0

    cols = ["ts", *COLS]
    placeholders = ", ".join(f":{column}" for column in cols)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in COLS
    )

    statement = text(
        f"""
        INSERT INTO demand_raw ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (ts) DO UPDATE SET {updates}
        """
    )

    with engine.begin() as connection:
        connection.execute(statement, records)

    return len(records)