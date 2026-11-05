
from sqlalchemy import text
from gridcast.db import get_engine

engine = get_engine()

with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT
            DATE(ts) AS demand_date,
            COUNT(*) AS records,
            MIN(ts) AS first_record,
            MAX(ts) AS last_record
        FROM demand_raw
        GROUP BY DATE(ts)
        ORDER BY demand_date DESC
        LIMIT 10;
    """)).fetchall()

for row in rows:
    print(row)
