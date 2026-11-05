import numpy as np
import pandas as pd
from gridcast.metrics import evaluate

STEP = pd.Timedelta(minutes=15)


def make_folds(index, n_folds, test_days, min_train_days):
    test_len = pd.Timedelta(days=test_days)
    end = index.max().normalize() + pd.Timedelta(days=1)      # fold edges land on midnight
    folds = []
    for i in range(n_folds):
        test_end = end - (n_folds - 1 - i) * test_len
        test_start = test_end - test_len
        if (test_start - index.min()) < pd.Timedelta(days=min_train_days):
            raise ValueError(f"Fold {i}: less than {min_train_days} days of training data.")
        train_idx = index[index < test_start]
        test_idx = index[(index >= test_start) & (index < test_end)]
        folds.append((train_idx, test_idx, test_start))
    return folds


def run_backtest(model, X, y, cfg, eval_mask=None):
    bt = cfg["backtest"]
    folds = make_folds(X.index, bt["n_folds"], bt["test_days"], bt["min_train_days"])
    peak = (bt["peak_start_hour"], bt["peak_end_hour"])

    rows, preds = [], []
    for k, (tr, te, test_start) in enumerate(folds):
        te_eval = te if eval_mask is None else te[eval_mask.loc[te].to_numpy()]
        model.fit(X.loc[tr], y.loc[tr])
        p = pd.Series(model.predict(X.loc[te_eval]), index=te_eval)
        m = evaluate(y.loc[te_eval], p, y.loc[tr], peak)
        rows.append({"fold": k, "test_start": test_start.date(),
                     "n_test": len(te), "n_eval": len(te_eval), **m})
        preds.append(pd.DataFrame({"y": y.loc[te_eval], "pred": p, "fold": k}))
    return pd.DataFrame(rows), pd.concat(preds)