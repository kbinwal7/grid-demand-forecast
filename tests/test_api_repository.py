import os
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from gridcast.api.repository import ForecastRepository


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason=(
        "Set TEST_DATABASE_URL to an initialized test database to run SQL repository "
        "tests against PostgreSQL."
    ),
)
def test_repository_queries_run_against_postgres():
    database_url = make_url(os.environ["TEST_DATABASE_URL"])
    if database_url.drivername == "postgresql":
        database_url = database_url.set(drivername="postgresql+psycopg")
    engine = create_engine(database_url, pool_pre_ping=True)
    repository = ForecastRepository(engine)
    try:
        assert isinstance(repository.models(), list)
        assert isinstance(
            repository.forecast(date(2099, 1, 1), "missing-model", "backtest"),
            list,
        )
        assert isinstance(
            repository.actuals(date(2099, 1, 1), date(2099, 1, 1)),
            list,
        )
        assert isinstance(
            repository.accuracy_daily(
                date(2099, 1, 1), date(2099, 1, 1), "missing-model", "backtest"
            ),
            list,
        )
        assert isinstance(
            repository.accuracy_heatmap(
                date(2099, 1, 1), date(2099, 1, 1), "missing-model", "backtest"
            ),
            list,
        )
        assert isinstance(
            repository.worst_days(
                10,
                date(2099, 1, 1),
                date(2099, 1, 1),
                "missing-model",
                "backtest",
            ),
            list,
        )
        assert isinstance(
            repository.coverage(
                date(2099, 1, 1), date(2099, 1, 1), "missing-model", "backtest"
            ),
            list,
        )
        assert isinstance(repository.data_health(), dict)
    finally:
        engine.dispose()
