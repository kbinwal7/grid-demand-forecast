
from sqlalchemy import text
from gridcast.db import get_engine

engine = get_engine()

sql = """
CREATE TABLE IF NOT EXISTS model_artifacts (
    model_id TEXT PRIMARY KEY,
    payload BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

with engine.begin() as connection:
    connection.execute(text(sql))

print("SUCCESS: model_artifacts table is ready.")
