from pathlib import Path

from gridcast.db import get_engine


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "003_fallback_forecasts.sql"


def main():
    engine = get_engine()
    statements = [
        statement.strip()
        for statement in MIGRATION.read_text(encoding="utf-8").split(";")
    ]
    with engine.begin() as connection:
        for statement in statements:
            if statement:
                connection.exec_driver_sql(statement)
    print("Fallback forecast database migration applied.")


if __name__ == "__main__":
    main()
