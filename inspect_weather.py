
from sqlalchemy import text
from gridcast.db import get_engine

engine = get_engine()

with engine.connect() as conn:
    print("\nLATEST WEATHER RECORDS:")

    rows = conn.execute(text("""
        SELECT
            DATE(ts) AS weather_date,
            COUNT(*) AS records,
            MIN(ts) AS first_record,
            MAX(ts) AS last_record
        FROM weather_forecast
        GROUP BY DATE(ts)
        ORDER BY weather_date DESC
        LIMIT 15;
    """)).fetchall()

    for row in rows:
        print(row)

    print("\nMISSING WEATHER DAYS NEAR NOVEMBER 5:")

    rows = conn.execute(text("""
        SELECT COUNT(*)
        FROM weather_forecast
        WHERE ts >= '2026-11-04'
          AND ts < '2026-11-06';
    """)).fetchone()

    print("Records for Nov 4-5:", rows[0])
