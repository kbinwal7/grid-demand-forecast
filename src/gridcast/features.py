import numpy as np
import pandas as pd
import holidays

from gridcast.weather import engineer_weather_features


# ---------------------------------------------------------------- feature groups (for ablation)
FEATURE_GROUPS = {
    "calendar": ["interval_of_day", "day_of_week", "is_weekend"],
    "holidays": ["is_holiday", "days_to_holiday", "days_since_holiday"],
    "demand_lags": ["demand_met_mw_lag288", "demand_met_mw_lag672"],
    "rolling": ["demand_roll96_mean", "demand_roll96_std",
                "demand_roll672_mean", "demand_roll672_std"],
    "gen_lags": ["solar_mw_lag288", "solar_mw_lag672", "wind_mw_lag288", "wind_mw_lag672"],
    "weather": ["weighted_temperature_2m", "weighted_relative_humidity_2m",
                "weighted_apparent_temperature", "weighted_dew_point_2m",
                "cdd", "app_cdd", "temp_day_max", "temp_day_mean", "temp_max_3d_mean"],
}


def select_groups(X: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    cols = [c for g in groups for c in FEATURE_GROUPS[g]]
    missing = [c for c in cols if c not in X.columns]
    if missing:
        raise KeyError(f"Columns not in X (use_weather false? feature renamed?): {missing}")
    return X[cols].copy()


# ---------------------------------------------------------------- leakage guard
def _check_lags(lags: list[int], horizon: int) -> None:
    """Require lags to respect the conservative D-3 data-availability cutoff.

    The forecast for day D is issued on D-1. The source currently does not
    reliably publish D-2 actual demand by then, so the latest usable actuals
    are from D-3. With 15-minute data this requires horizon + 192 steps.
    """
    minimum = horizon + 192
    bad = [lag for lag in lags if lag < minimum]
    if bad:
        raise ValueError(
            f"Lags {bad} are too short for the D-3 data cutoff: "
            f"minimum lag is horizon + 192 steps ({minimum})."
        )


# ---------------------------------------------------------------- calendar
def add_calendar(df: pd.DataFrame, use_month: bool = False) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["interval_of_day"] = df.index.hour * 4 + df.index.minute // 15   # 0..95
    out["day_of_week"] = df.index.dayofweek                               # 0 = Monday
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)
    if use_month:
        out["month"] = df.index.month
    return out


# ---------------------------------------------------------------- holidays
def add_holidays(df: pd.DataFrame, clip: int = 7) -> pd.DataFrame:
    """is_holiday, plus days to the next holiday and since the last one (clipped).
    Holidays are known in advance, so these features are leakage-safe."""
    days = pd.DatetimeIndex(df.index.normalize().unique())

    # Pad the year range by +-1 so a "next" and "previous" holiday always exists,
    # even on the first and last days of the dataset.
    cal = holidays.India(years=range(days.min().year - 1, days.max().year + 2))
    hol = pd.DatetimeIndex(sorted(pd.to_datetime(list(cal.keys()))))

    nxt = hol[hol.searchsorted(days, side="left")]           # first holiday >= day
    prv = hol[hol.searchsorted(days, side="right") - 1]      # last holiday <= day

    d = pd.DataFrame(index=days)
    d["is_holiday"] = days.isin(hol).astype(int)
    d["days_to_holiday"] = np.minimum(np.asarray((nxt - days).days), clip)
    d["days_since_holiday"] = np.minimum(np.asarray((days - prv).days), clip)

    return d.loc[df.index.normalize()].set_axis(df.index)


# ---------------------------------------------------------------- lags / rolling
def add_lags(df: pd.DataFrame, cols: list[str], lags: list[int], horizon: int) -> pd.DataFrame:
    _check_lags(lags, horizon)
    out = {f"{c}_lag{l}": df[c].shift(l) for c in cols for l in lags}
    return pd.DataFrame(out, index=df.index)


def add_rolling(s: pd.Series, windows: list[int], horizon: int, name: str) -> pd.DataFrame:
    # Conservative D-3 cutoff: shift beyond the forecast horizon by a further
    # two days so the rolling statistics use only data through D-3.
    known = s.shift(horizon + 192)
    out = {}
    for w in windows:
        r = known.rolling(w)
        out[f"{name}_roll{w}_mean"] = r.mean()
        out[f"{name}_roll{w}_std"] = r.std()
    return pd.DataFrame(out, index=s.index)


# ---------------------------------------------------------------- weather
def add_weather(df: pd.DataFrame, weather: pd.DataFrame, cdd_base_c: float = 24.0) -> pd.DataFrame:
    """Join forecast-weather features onto the demand index. Days without weather become NaN.
    No shift is needed: the day's forecast is issued before the day starts (see weather.resolve_run)."""
    return engineer_weather_features(weather, cdd_base_c).reindex(df.index)


# ---------------------------------------------------------------- assembly
def build_features(df, cfg, weather=None, drop_unlabeled=True):
    f, h = cfg["features"], cfg["forecast"]["horizon_steps"]
    target = cfg["forecast"]["target"]

    use_weather = f.get("use_weather", False)
    if use_weather and weather is None:
        raise ValueError(
            "features.use_weather is true but no weather table was passed. "
            "Pass weather=..., or set cfg['features']['use_weather'] = False on a copy of the config."
        )

    parts = [
        add_calendar(df, f["use_month"]),
        add_holidays(df),
        add_lags(df, [target], f["demand_lags"], h),
        add_rolling(df[target], f["rolling_windows"], h, "demand"),
        add_lags(df, f["gen_cols"], f["gen_lags"], h),
    ]
    if use_weather:
        parts.append(add_weather(df, weather, f.get("cdd_base_c", 24.0)))

    X = pd.concat(parts, axis=1)
    y = df[target]

    # 1) Warm-up: remove rows before all configured lag/rolling inputs can exist.
    warmup = max(
        max(f["demand_lags"], default=0),
        max(f["gen_lags"], default=0) if f["gen_cols"] else 0,
        h + 192 + max(f["rolling_windows"], default=0),
    )
    X, y = X.iloc[warmup:], y.iloc[warmup:]

    # 2) Drop only rows whose TARGET is unknown. Gap-caused NaNs in features stay NaN
    #    (LightGBM handles them natively; baselines are scored via eval_mask).
    keep = y.notna() if drop_unlabeled else pd.Series(True, index=y.index)
    return X[keep], y[keep]
