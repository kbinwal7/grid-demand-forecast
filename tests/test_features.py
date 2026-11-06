import copy

import numpy as np
import pandas as pd
import pytest

from gridcast.features import (
    FEATURE_GROUPS, _check_lags, add_holidays, build_features, select_groups,
)

CFG = {
    "forecast": {"target": "demand_met_mw", "horizon_steps": 96},
    "features": {"demand_lags": [192, 672], "rolling_windows": [96],
                 "gen_cols": ["solar_mw"], "gen_lags": [192], "use_month": False,
                 "use_weather": False},
}


def make_df(n_days=30, seed=0):
    idx = pd.date_range("2024-01-01", periods=n_days * 96, freq="15min", name="datetime")
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"demand_met_mw": rng.normal(150_000, 5_000, len(idx)),
                         "solar_mw": rng.normal(10_000, 2_000, len(idx))}, index=idx)


def test_future_data_does_not_change_features():
    df = make_df()
    X1, _ = build_features(df, CFG)

    cut = df.index[20 * 96]                        # pretend the future starts here
    df2 = df.copy()
    df2.loc[df2.index >= cut, :] = 999_999.0       # corrupt everything from the cut onward
    X2, _ = build_features(df2, CFG)

    # A row at time t only uses data up to t - horizon, so rows before
    # (cut + horizon) must be identical even though the future was corrupted.
    safe = X1.index < cut + pd.Timedelta(minutes=15 * CFG["forecast"]["horizon_steps"])
    pd.testing.assert_frame_equal(X1[safe], X2.loc[X1.index[safe]])


def test_lags_that_depend_on_unpublished_d_minus_1_actuals_are_rejected():
    with pytest.raises(ValueError, match="minimum lag is horizon"):
        _check_lags([96, 192], horizon=96)


def test_day_ahead_rolling_features_only_use_d_minus_2_or_older():
    df = make_df(n_days=30)
    X1, _ = build_features(df, CFG)
    changed = df.copy()
    target_day = df.index[20 * 96].normalize()
    d_minus_1 = target_day - pd.Timedelta(days=1)
    changed.loc[d_minus_1:d_minus_1 + pd.Timedelta(hours=23, minutes=45), :] = 999_999
    X2, _ = build_features(changed, CFG)
    target_rows = X1.index.normalize() == target_day
    pd.testing.assert_frame_equal(X1.loc[target_rows], X2.loc[target_rows])


def test_holiday_features_have_expected_values():
    idx = pd.date_range("2025-01-20", "2025-01-30", freq="15min", name="datetime")
    out = add_holidays(pd.DataFrame(index=idx))
    assert out.loc["2025-01-26", "is_holiday"].eq(1).all()            # Republic Day
    assert out.loc["2025-01-26", "days_to_holiday"].eq(0).all()
    assert out.loc["2025-01-25", "days_to_holiday"].eq(1).all()


def test_use_weather_without_table_raises():
    cfg = copy.deepcopy(CFG)
    cfg["features"]["use_weather"] = True
    with pytest.raises(ValueError, match="use_weather"):
        build_features(make_df(), cfg)


def test_select_groups_reports_missing_columns():
    X, _ = build_features(make_df(), CFG)
    with pytest.raises(KeyError):
        select_groups(X, ["weather"])               # weather columns don't exist in this X
    assert select_groups(X, ["calendar"]).columns.tolist() == FEATURE_GROUPS["calendar"]