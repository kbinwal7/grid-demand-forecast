
from gridcast.db import get_engine
from sqlalchemy import text

engine = get_engine()

queries = {
    "MODEL_VERSIONS COLUMNS": """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'model_versions'
        ORDER BY ordinal_position
    """,
    "FORECAST TABLES": """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name ILIKE '%forecast%'
        ORDER BY table_name
    """,
}

with engine.connect() as connection:
    for title, query in queries.items():
        print(f"\n--- {title} ---")
        rows = connection.execute(text(query)).fetchall()
        for row in rows:
            print(row)
        if not rows:
            print("No results found.")
