from pathlib import Path
import numpy as np
import pandas as pd

# Columns the scraper writes that are bookkeeping, not features
META_COLS = ["date", "block", "time", "source_file"]


def load_raw(path: str | Path) -> pd.DataFrame:
    """Read the scraped PSP CSV -> clean DataFrame indexed by 15-min `datetime`."""
    df = pd.read_csv(path)

    # `timestamp` is ISO (YYYY-MM-DD HH:MM:SS), so there is no day/month ambiguity.
    df["datetime"] = pd.to_datetime(df["timestamp"], format="ISO8601")

    df = (
        df.drop(columns=["timestamp"] + [c for c in META_COLS if c in df.columns])
          .sort_values("datetime")
          .drop_duplicates(subset="datetime", keep="last")
          .set_index("datetime")
    )
    return df


def make_regular(df: pd.DataFrame, freq: str = "15min", max_gap: int = 4) -> pd.DataFrame:
    """Reindex to a gap-free grid, fill short gaps, leave long gaps as NaN."""
    out = df.copy()

    # Demand can't be zero or negative on a national grid -> sensor error.
    out.loc[out["demand_met_mw"] <= 0, "demand_met_mw"] = np.nan

    full_idx = pd.date_range(out.index.min(), out.index.max(), freq=freq, name="datetime")
    out = out.reindex(full_idx)

    is_na = out["demand_met_mw"].isna()
    out["was_missing"] = is_na            # record of what we repaired

    # Length of each run of consecutive NaNs
    run_id = (is_na != is_na.shift()).cumsum()
    run_len = is_na.groupby(run_id).transform("sum")
    long_gap = is_na & (run_len > max_gap)

    num_cols = out.select_dtypes("number").columns
    out[num_cols] = out[num_cols].interpolate(limit_area="inside")
    out.loc[long_gap, num_cols] = np.nan  # undo the fill for long outages
    return out