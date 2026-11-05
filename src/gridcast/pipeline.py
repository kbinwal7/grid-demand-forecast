
"""Data loading and feature pipeline helpers."""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from gridcast.data import load_raw, make_regular
from gridcast.features import build_features
from gridcast.store import read_demand, read_weather
from gridcast.weather import load_weather, weather_table_path


METRICS = ["mape", "peak_mape", "mase"]


def load_clean(cfg, engine=None):
    raw = (
        read_demand(engine)
        if engine is not None
        else load_raw(Path(cfg["root"]) / cfg["data"]["raw_path"])
    )

    return make_regular(
        raw,
        cfg["data"]["freq"],
        cfg["data"]["max_gap_steps"],
    )


def make_xy(df, cfg, use_weather=False, engine=None):
    cfg_ = copy.deepcopy(cfg)
    cfg_["features"]["use_weather"] = use_weather

    weather = None

    if use_weather:
        weather = (
            read_weather(engine)
            if engine is not None
            else load_weather(weather_table_path(cfg))
        )

    return build_features(df, cfg_, weather=weather)


def comparable_rows(X, cfg):
    target = cfg["forecast"]["target"]

    required = [
        f"{target}_lag96",
        f"{target}_lag672",
    ]

    missing = [c for c in required if c not in X.columns]

    if missing:
        raise ValueError(f"Missing baseline columns: {missing}")

    return X[required].notna().all(axis=1)