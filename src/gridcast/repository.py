import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gridcast.db import engine
from gridcast.db_models import Actual, Forecast, ModelVersion


def save_actuals(data: pd.DataFrame) -> None:
    """Insert or update actual demand observations."""

    records = [
        {
            "timestamp": timestamp.to_pydatetime(),
            "demand_met_mw": float(value),
        }
        for timestamp, value in data["demand_met_mw"].dropna().items()
    ]

    if not records:
        return

    statement = insert(Actual).values(records)
    statement = statement.on_conflict_do_update(
        index_elements=["timestamp"],
        set_={"demand_met_mw": statement.excluded.demand_met_mw},
    )

    with engine.begin() as connection:
        connection.execute(statement)


def load_actuals(start=None, end=None) -> pd.DataFrame:
    """Load actual demand observations."""

    statement = select(
        Actual.timestamp,
        Actual.demand_met_mw,
    ).order_by(Actual.timestamp)

    if start is not None:
        statement = statement.where(Actual.timestamp >= start)

    if end is not None:
        statement = statement.where(Actual.timestamp <= end)

    with engine.connect() as connection:
        data = pd.read_sql(statement, connection)

    if not data.empty:
        data = data.set_index("timestamp")

    return data


def save_model_version(
    model_name: str,
    version: str,
    trained_at,
    metrics: dict | None = None,
    parameters: dict | None = None,
) -> int:
    """Save model metadata and return its database ID."""

    record = ModelVersion(
        model_name=model_name,
        version=version,
        trained_at=trained_at,
        metrics=metrics,
        parameters=parameters,
    )

    with engine.begin() as connection:
        from sqlalchemy.orm import Session

        with Session(bind=connection) as session:
            session.add(record)
            session.flush()
            return record.id