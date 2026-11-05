from pathlib import Path

from gridcast.config import find_repo_root, load_config
from gridcast.data import load_raw
from gridcast.weather import build_weather_history

ROOT = find_repo_root(Path(__file__))
cfg = load_config(ROOT / "configs" / "config.yaml")

raw = load_raw(Path(cfg["root"]) / cfg["data"]["raw_path"])
days = raw.index.normalize().unique()
print(f"Repo root: {ROOT}\nDays in demand data: {len(days)}")

weather, failures = build_weather_history(days, cfg)

print(f"\nFailures: {len(failures)}")
for day, msg in failures[:10]:
    print(f"  {day.date()}: {msg[:160]}")