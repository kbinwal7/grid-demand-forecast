"""Traffic-light rules for the dashboard KPI tiles ("green" / "yellow" / "red" / "neutral")."""

import math

PEAK_HOURS = (18, 22)  # evening peak window, IST [start, end)
KPI_WINDOW_DAYS = 7
BASELINE_MODEL_ID ="blend_1d_1w"  # simple lag-average baseline every model must beat
MAPE_GREEN_FACTOR = 1.15  # green if within 15% of the champion's backtest MAPE...
COVERAGE_GREEN = (75.0, 85.0)  # q10-q90 should contain ~80% of actuals
COVERAGE_YELLOW = (70.0, 90.0)
MIN_COVERAGE_POINTS = 288  # 3 days of 15-min intervals before judging coverage
STALE_AFTER_DAYS = 3


def _finite(*values) -> bool:
    return all(v is not None and math.isfinite(v) and v >= 0 for v in values)


def mape_status(value: float | None, champion: float | None, baseline: float | None) -> str:
    """...and never worse than the baseline. Worse than the baseline is red."""
    if not _finite(value, champion, baseline):
        return "neutral"
    if value <= min(MAPE_GREEN_FACTOR * champion, baseline):
        return "green"
    return "yellow" if value <= baseline else "red"


def coverage_status(pct: float | None, n: int) -> str:
    if pct is None or not math.isfinite(pct) or n < MIN_COVERAGE_POINTS:
        return "neutral"
    if COVERAGE_GREEN[0] <= pct <= COVERAGE_GREEN[1]:
        return "green"
    if COVERAGE_YELLOW[0] <= pct <= COVERAGE_YELLOW[1]:
        return "yellow"
    return "red"


def freshness_status(lag_days: int | None, missing_days: int) -> str:
    if lag_days is None:
        return "neutral"
    if lag_days > STALE_AFTER_DAYS:
        return "red"
    return "yellow" if missing_days else "green"
