import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def initialize_database(
    engine: Engine,
    app_role: str,
    app_password: str,
) -> None:
    """Apply the idempotent application schema and configure its read-only role."""
    if not app_password:
        raise ValueError(
            "APP_DB_PASSWORD must be set to create the read-only app role."
        )

    schema = _read_sql(PROJECT_ROOT / "sql" / "schema.sql")
    role_setup = _read_sql(PROJECT_ROOT / "sql" / "roles.sql")
    statements = [statement.strip() for statement in schema.split(";")]

    with engine.begin() as connection:
        for statement in statements:
            if statement:
                connection.exec_driver_sql(statement)

        connection.execute(
            text(
                "SELECT set_config('gridcast.app_role', :role, true), "
                "set_config('gridcast.app_password', :password, true)"
            ),
            {"role": app_role, "password": app_password},
        )
        connection.exec_driver_sql(role_setup)


def main() -> None:
    from gridcast.db import get_engine

    engine = get_engine()

    try:
        initialize_database(
            engine,
            app_role=os.getenv("APP_DB_USER", "gridcast_reader"),
            app_password=os.getenv("APP_DB_PASSWORD", ""),
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
