from pathlib import Path
import yaml

REQUIRED = {
    "data": ["raw_path", "freq", "max_gap_steps"],
    "forecast": ["target", "horizon_steps"],
    "features": ["demand_lags", "rolling_windows", "gen_cols", "gen_lags", "use_month"],
    "backtest": ["n_folds", "test_days", "min_train_days", "peak_start_hour", "peak_end_hour"],
    "weather": ["model", "run_hour_utc", "run_offset_days", "timezone", "locations"],
}


def check_config(cfg: dict) -> None:
    """Report ALL missing keys at once instead of failing deep inside some function."""
    problems = []
    for section, keys in REQUIRED.items():
        if section not in cfg:
            problems.append(f"missing section: {section}")
            continue
        problems += [f"{section}.{k} is missing" for k in keys if k not in cfg[section]]
    if problems:
        raise KeyError("config.yaml problems:\n  - " + "\n  - ".join(problems))


def load_config(path: str | Path = "configs/config.yaml") -> dict:
    path = Path(path).resolve()
    with open(path) as f:
        cfg = yaml.safe_load(f)
    check_config(cfg)
    # repo root = parent of the configs/ folder; used to resolve relative data paths
    # no matter whether code runs from the repo root or from notebooks/
    cfg["root"] = str(path.parent.parent)
    return cfg


def find_repo_root(start: str | Path | None = None) -> Path:
    """Walk upward from `start` until a folder containing configs/config.yaml is found."""
    here = Path(start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        if (p / "configs" / "config.yaml").exists():
            return p
    raise FileNotFoundError(f"No configs/config.yaml found above {here}")