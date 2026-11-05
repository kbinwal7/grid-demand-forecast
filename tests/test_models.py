import numpy as np
import pandas as pd

from gridcast.models import Blend, LightGBMModel, SeasonalNaive


def _toy(n_days=60):
    idx = pd.date_range("2025-01-01", periods=n_days * 96, freq="15min", name="datetime")
    rng = np.random.default_rng(0)
    hod = (idx.hour * 4 + idx.minute // 15).to_numpy()
    X = pd.DataFrame({"interval_of_day": hod, "noise": rng.normal(size=len(idx))}, index=idx)
    y = pd.Series(100 + 20 * np.sin(hod / 96 * 2 * np.pi) + rng.normal(0, 1, len(idx)), index=idx)
    return X, y


def test_lightgbm_learns_a_daily_pattern():
    X, y = _toy()
    tr, te = X.index < "2025-02-15", X.index >= "2025-02-15"
    m = LightGBMModel(seed=0, max_rounds=300).fit(X[tr], y[tr])
    mae = np.abs(m.predict(X[te]) - y[te]).mean()
    assert mae < 3          # noise std is 1; predicting the mean would give MAE ~ 13


def test_predict_ignores_column_order():
    X, y = _toy()
    tr, te = X.index < "2025-02-15", X.index >= "2025-02-15"
    m = LightGBMModel(seed=0, max_rounds=100).fit(X[tr], y[tr])
    shuffled = X[te][["noise", "interval_of_day"]]
    assert np.allclose(m.predict(X[te]), m.predict(shuffled))


def test_same_seed_gives_same_predictions():
    X, y = _toy()
    a = LightGBMModel(seed=1, max_rounds=100).fit(X, y).predict(X)
    b = LightGBMModel(seed=1, max_rounds=100).fit(X, y).predict(X)
    assert np.allclose(a, b)


def test_baselines():
    X = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert SeasonalNaive("a").predict(X).tolist() == [1.0, 2.0]
    assert Blend(["a", "b"]).predict(X).tolist() == [2.0, 3.0]