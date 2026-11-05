import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.init_db import initialize_database


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Set TEST_DATABASE_URL to run the PostgreSQL initialization test.",
)
def test_initialization_is_idempotent():
    database_url = make_url(os.environ["TEST_DATABASE_URL"])
    if database_url.drivername == "postgresql":
        database_url = database_url.set(drivername="postgresql+psycopg")
    app_password = os.getenv("TEST_APP_DB_PASSWORD")
    if not app_password:
        pytest.skip(
            "Set TEST_APP_DB_PASSWORD to run the role-creation portion of this test."
        )

    engine = create_engine(database_url, pool_pre_ping=True)
    app_role = os.getenv("TEST_APP_DB_USER", "gridcast_test_reader")
    try:
        initialize_database(engine, app_role, app_password)
        initialize_database(engine, app_role, app_password)

        with engine.connect() as connection:
            tables = connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars()
            assert {
                "demand_raw",
                "weather_forecast",
                "model_versions",
                "forecasts",
            } <= set(tables)
            assert (
                connection.execute(
                    text("SELECT to_regclass('public.forecast_vs_actual')")
                ).scalar_one()
                == "forecast_vs_actual"
            )
    finally:
        engine.dispose()
