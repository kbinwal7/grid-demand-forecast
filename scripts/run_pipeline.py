import argparse
from pathlib import Path

import pandas as pd

from gridcast import jobs
from gridcast.config import load_config
from gridcast.db import get_engine

ROOT = Path(__file__).resolve().parents[1]

p = argparse.ArgumentParser()
p.add_argument("job", choices=["bootstrap", "daily", "weekly", "retrain", "forecast"])
p.add_argument("--date", help="target day for 'forecast' (YYYY-MM-DD)")
a = p.parse_args()

cfg, engine = load_config(ROOT / "configs" / "config.yaml"), get_engine()
if a.job == "bootstrap":
    print("demand rows, weather rows:", jobs.bootstrap(engine, cfg))
elif a.job == "daily":
    jobs.run_daily(engine, cfg)
elif a.job == "weekly":
    jobs.run_weekly(engine, cfg)
elif a.job == "retrain":
    jobs.retrain(engine, cfg)
else:
    jobs.forecast_day(engine, cfg, a.date or jobs.now_local(cfg).normalize())