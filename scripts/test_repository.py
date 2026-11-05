import pandas as pd
from sqlalchemy import delete

from gridcast.db import engine
from gridcast.db_models import Actual
from gridcast.repository import save_actuals, load_actuals

# Use dates far in the future so these test records
# cannot be confused with real historical observations.
timestamps = pd.date_range(
    "2099-01-01 00:00",
    periods=3,
    freq="15min",
)

sample = pd.DataFrame(
    {"demand_met_mw": [150000.0, 151000.0, 152000.0]},
    index=timestamps,
)
sample.index.name = "timestamp"

try:
    # Write test records
    save_actuals(sample)

    # Read them back
    result = load_actuals(
        start=timestamps[0].to_pydatetime(),
        end=timestamps[-1].to_pydatetime(),
    )

    print("Records read:", len(result))
    print(result)

    assert len(result) == 3
    assert result["demand_met_mw"].tolist() == [150000.0, 151000.0, 152000.0]

    print("\nDatabase write/read test passed!")

finally:
    # Always remove the synthetic test records
    with engine.begin() as connection:
        connection.execute(
            delete(Actual).where(Actual.timestamp.in_(timestamps))
        )

    print("Temporary test records cleaned up.")