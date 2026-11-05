import numpy as np
import pandas as pd


def mae(y, p):  return float(np.mean(np.abs(y - p)))
def rmse(y, p): return float(np.sqrt(np.mean((y - p) ** 2)))
def mape(y, p): return float(np.mean(np.abs((y - p) / y)) * 100)


def mase(y, p, y_train, season=96):
    """MAE scaled by how well 'same time yesterday' does on the training data.
    < 1 means we beat the seasonal-naive method; > 1 means we're worse."""
    naive = y_train.shift(season, freq="15min")
    both = pd.concat([y_train, naive], axis=1, join="inner").dropna()
    scale = np.mean(np.abs(both.iloc[:, 0] - both.iloc[:, 1]))
    return float(np.mean(np.abs(y - p)) / scale)


def evaluate(y: pd.Series, p: pd.Series, y_train: pd.Series,
             peak_hours: tuple[int, int] = (18, 22)) -> dict:
    peak = (y.index.hour >= peak_hours[0]) & (y.index.hour < peak_hours[1])
    return {
        "mae": mae(y, p),
        "rmse": rmse(y, p),
        "mape": mape(y, p),
        "mase": mase(y, p, y_train),
        "peak_mape": mape(y[peak], p[peak]),
    }