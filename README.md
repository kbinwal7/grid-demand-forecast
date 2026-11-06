# GridCast

Day-ahead electricity demand forecasting for the Indian grid at 15-minute resolution
(96 intervals per day). A learning project: PostgreSQL stores demand, weather, models and
forecasts; a FastAPI app serves a read-only JSON API and a one-page dashboard.

```
Grid-India scraper ─┐                              ┌─ /v1/* JSON API ──┐
Open-Meteo weather ─┼─► jobs / scripts ─► Postgres ─► FastAPI          ├─► dashboard (Plotly)
LightGBM training  ─┘    (write role)               (read-only role)  └─ /  HTML page ───┘
```

## Project layout

```
gc/
├── configs/config.yaml        # pipeline settings (features, backtest, weather, jobs)
├── sql/
│   ├── schema.sql             # all tables + forecast_vs_actual view (idempotent)
│   └── roles.sql              # read-only role for the web app
├── scripts/
│   ├── init_db.py             # apply schema + create read-only role
│   ├── seed_demo.py           # synthetic demo data (no scraping needed)
│   ├── run_pipeline.py        # real pipeline: bootstrap | daily | weekly | retrain | forecast
│   ├── load_forecasts.py      # load notebook backtest parquet into the DB
│   └── backfill_weather.py    # historical weather download
├── src/gridcast/
│   ├── api/                   # ── BACKEND ──
│   │   ├── main.py            #   app factory, error handlers, headers, static mount
│   │   ├── routes_api.py      #   /health and /v1/* endpoints
│   │   ├── routes_pages.py    #   GET / -> dashboard.html
│   │   ├── repository.py      #   every SQL query the API runs
│   │   ├── kpis.py            #   green/yellow/red rules for KPI tiles
│   │   ├── schemas.py         #   response models (the API contract)
│   │   └── deps.py            #   read-only DB engine
│   ├── templates/dashboard.html   # ── FRONTEND ── one page
│   ├── static/css/app.css
│   ├── static/js/dashboard.js     #   fetch /v1 -> render tiles + charts
│   ├── static/js/api.js           #   fetch helper
│   ├── static/vendor/             #   Plotly.js (vendored, no CDN/npm)
│   └── *.py                   # ── ML PIPELINE ── data, features, models, backtest, jobs, scraper, weather, store
├── notebooks/                 # exploration
└── tests/
```

## Setup (Windows / PowerShell)

Requires Python 3.12+ and a running PostgreSQL.

```powershell
# 1. virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"            # add ,notebooks for Jupyter + matplotlib

# 2. database (once, in psql as the postgres superuser)
#    CREATE DATABASE gridcast;
```

Create `.env` in the project root:

```ini
# write access: init_db, seed_demo, pipeline jobs
ADMIN_DATABASE_URL=postgresql://postgres:<your-postgres-password>@localhost:5432/gridcast
# read-only role created by init_db; used by the web app
APP_DB_USER=gridcast_reader
APP_DB_PASSWORD=<pick-a-password>
DATABASE_URL=postgresql://gridcast_reader:<same-password>@localhost:5432/gridcast
```

URL-encode special characters in passwords (`@` → `%40`, `#` → `%23`).

```powershell
# 3. tables + read-only role, then demo data
python scripts/init_db.py
python scripts/seed_demo.py

# 4. run
uvicorn gridcast.api.main:app --reload
```

- Dashboard: <http://127.0.0.1:8000/>
- API docs (try every endpoint): <http://127.0.0.1:8000/docs>

Tests: `pytest -q`

## The dashboard

One page, two filters (model, forecast day). Everything else follows from them.

| Element | What it tells you | API |
| --- | --- | --- |
| **Expected peak** | max forecast MW for the day and its time | `/v1/summary` |
| **Max temperature** | main demand driver (cooling load) | `/v1/summary` |
| **MAPE · last 7 days** | average % error; green = near the champion's backtest, red = worse than the naive baseline | `/v1/summary` |
| **Interval coverage** | share of actuals inside q10–q90; ~80% means well-calibrated uncertainty | `/v1/summary` |
| **Data freshness** | days since the latest published actual, plus gaps | `/v1/summary` |
| Forecast vs actual | 96-interval forecast, uncertainty band, actual overlay | `/v1/day` |
| Daily error | daily MAPE and evening-peak (18–22h) MAPE over 30 days | `/v1/accuracy/daily` |
| Bias heatmap | systematic over/under-forecast by weekday × hour | `/v1/accuracy/heatmap` |

`model_id` defaults to the champion and `date` to that model's latest forecast day. Where a
model has both a `live` and a `backtest` forecast for the same interval, `live` wins.

## Real pipeline (optional)

`seed_demo.py` is enough to explore the app. To use real data instead:

```powershell
python scripts/run_pipeline.py bootstrap   # scrape Grid-India + weather history into Postgres
python scripts/run_pipeline.py retrain     # LightGBM walk-forward backtest, register + promote
python scripts/run_pipeline.py daily       # ingest latest actuals, forecast tomorrow
python -m gridcast.experiment --model xgboost   # non-promoting experiment
```

**Information cutoff:** the forecast for day D is issued on D-1, and Grid-India publishes D-1
actuals only after D-1 ends. So the features use demand only through D-2 (192- and
672-step lags). Training, backtesting and live inference all use the same cutoff.

Timestamps are naive IST (`TIMESTAMP`), and the browser shows them as-is.

Weather data by [Open-Meteo.com](https://open-meteo.com/).
