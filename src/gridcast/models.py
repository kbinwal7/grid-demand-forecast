import lightgbm as lgb
import pandas as pd

DEFAULT_PARAMS = dict(
    objective="regression", learning_rate=0.05, num_leaves=63,
    min_child_samples=50, subsample=0.8, subsample_freq=1,
    colsample_bytree=0.8, reg_lambda=1.0, n_jobs=-1, verbose=-1,
    deterministic=True, force_row_wise=True,       # same inputs -> same model
)


# ------------------------------------------------------------------ baselines
class SeasonalNaive:
    """Predict with the value from one lag column, e.g. same time yesterday."""

    def __init__(self, col: str):
        self.col = col

    def fit(self, X, y):
        return self                                # nothing to learn

    def predict(self, X):
        return X[self.col].to_numpy()


class Blend:
    """Average of several lag columns, e.g. yesterday and last week."""

    def __init__(self, cols: list[str]):
        self.cols = cols

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X[self.cols].mean(axis=1).to_numpy()


# ------------------------------------------------------------------ LightGBM
class LightGBMModel:
    """LightGBM with early stopping on the LAST `val_days` of the training window only.
    The test fold is never touched during fitting."""

    def __init__(self, params=None, val_days=14, max_rounds=3000,
                 patience=100, refit=True, seed=42):
        self.params = {**DEFAULT_PARAMS, **(params or {}), "random_state": seed}
        self.val_days, self.max_rounds = val_days, max_rounds
        self.patience, self.refit = patience, refit

    def fit(self, X, y):
        self.columns_ = list(X.columns)
        cut = X.index.max() - pd.Timedelta(days=self.val_days)
        is_tr = X.index <= cut                       # time-based split, never random
        if is_tr.sum() == 0 or (~is_tr).sum() == 0:
            raise ValueError("Training window too short for the validation slice.")

        m = lgb.LGBMRegressor(**self.params, n_estimators=self.max_rounds)
        m.fit(X[is_tr], y[is_tr],
              eval_set=[(X[~is_tr], y[~is_tr])], eval_metric="l1",
              callbacks=[lgb.early_stopping(self.patience, first_metric_only=True, verbose=False),
                         lgb.log_evaluation(0)])
        self.best_iteration_ = m.best_iteration_ or self.max_rounds

        if self.refit:                               # retrain on ALL training data with the chosen rounds
            m = lgb.LGBMRegressor(**self.params, n_estimators=self.best_iteration_)
            m.fit(X, y)
        self.model_ = m
        return self

    def predict(self, X):
        return self.model_.predict(X[self.columns_])   # column order can't silently differ