import pytest

from gridcast.jobs import fallback_feature_columns


def test_fallback_features_exclude_recent_lags_and_rolling_inputs():
    columns = [
        "interval_of_day",
        "demand_met_mw_lag96",
        "demand_met_mw_lag192",
        "demand_met_mw_lag288",
        "demand_met_mw_lag672",
        "demand_roll96_mean",
        "solar_mw_lag96",
        "solar_mw_lag672",
        "weighted_temperature_2m",
    ]

    selected = fallback_feature_columns(columns, "demand_met_mw")

    for kept in ("demand_met_mw_lag288", "demand_met_mw_lag672", "solar_mw_lag672", "weighted_temperature_2m"):
        assert kept in selected
    for dropped in ("demand_met_mw_lag96", "demand_met_mw_lag192", "demand_roll96_mean", "solar_mw_lag96"):
        assert dropped not in selected


def test_fallback_features_requires_three_day_and_week_lags():
    with pytest.raises(RuntimeError, match="missing required historical lags"):
        fallback_feature_columns(["interval_of_day", "demand_met_mw_lag96"], "demand_met_mw")
