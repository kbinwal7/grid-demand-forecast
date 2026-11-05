# GridCast

Day-ahead electricity demand forecasting for the Indian grid at 15-minute
resolution (96 intervals per day). GridCast stores demand, weather, model
versions, and forecasts in PostgreSQL. One FastAPI process serves a read-only
JSON API and a server-rendered Jinja2 dashboard; the web app never loads a
forecasting model, builds features, or trains.

## Architecture

```mermaid
flowchart LR
    N[Notebooks and scheduled forecasting jobs] -->|backtest parquet or live forecast rows| W[Database loader or job]
    W -->|write connection| DB[(PostgreSQL 16 or Neon)]
    DB -->|read-only connection| APP[FastAPI application]
    APP --> API[/v1 JSON API]
    APP --> HTML[Jinja2 dashboard pages]
    HTML --> JS[Vanilla JavaScript ES modules]
    JS -->|same-origin fetch| API
    JS --> PLOT[Vendored Plotly.js 2.35.2]
```

Backtest predictions are historical evaluations, not live forecasts. API
responses and dashboard panels label their source as `backtest` or `live`;
combined data-health counts identify both sources.

Naive PostgreSQL `TIMESTAMP` values represent local Indian Standard Time (IST).
`TIMESTAMPTZ` values represent instants and are displayed in IST where shown.
The browser does not reinterpret naive forecast timestamps as its own local
timezone.

## Local development (PowerShell)

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), and Docker Desktop.

```powershell
Copy-Item .env.example .env
notepad .env
```

Set different strong values for the local database owner and read-only app
passwords. The example config uses `ADMIN_DATABASE_URL` for schema and forecast
writes and `DATABASE_URL` for the web app's read-only role. URL-encode reserved
characters in passwords embedded in either connection URL.

```powershell
uv sync
docker compose up -d db
uv run python scripts/init_db.py
```

`init_db.py` is idempotent. It applies `sql/schema.sql` and configures the
read-only role from `APP_DB_USER` and `APP_DB_PASSWORD`.

Load a notebook-produced parquet file after the demand history used for MASE is
present in `demand_raw`:

```powershell
uv run python scripts/load_forecasts.py `
  --model-id blend_1d_1w `
  --preds data\processed\backtest_preds_blend_1d_1w.parquet `
  --source backtest `
  --trained-at 2026-11-05T00:00:00+05:30 `
  --champion
```

Run the app and open the dashboard, API docs, or health endpoint:

```powershell
uv run uvicorn gridcast.api.main:app --host 127.0.0.1 --port 8000
```

- Dashboard: <http://127.0.0.1:8000/>
- JSON API and OpenAPI docs: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

The pages are Forecast (`/`), Accuracy (`/accuracy`), Failures (`/failures`),
Models (`/models`), and Data health (`/data-health`). Dashboard charts use
same-origin API requests and the locally vendored Plotly.js bundle. No npm,
frontend build, runtime CDN, or second web service is used.

Responses larger than 500 bytes are gzip-compressed. Successful `/v1` JSON GET
responses include ETags for conditional requests; static files are cached for
one hour. The CSP keeps scripts and assets same-origin and uses a random
per-response style nonce for Plotly's generated stylesheet (without
`unsafe-inline`).

Run tests with `uv run python -m pytest -q`. PostgreSQL integration tests run
only when `TEST_DATABASE_URL` points to an initialized test database.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Required by the web app; PostgreSQL URL for a **read-only** role. Use Neon's pooled URL in production. |
| `ADMIN_DATABASE_URL` | Write-capable URL used by `scripts/init_db.py` and forecast-loading jobs. Do not configure this on the web service. |
| `APP_DB_USER`, `APP_DB_PASSWORD` | Role name and password created for read-only app access by `scripts/init_db.py`. |
| `ALLOWED_HOSTS` | Comma-separated trusted hostnames; include the Render `*.onrender.com` host. |
| `RATE_LIMIT` | SlowAPI limit, for example `60/minute`. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT` | Local Compose database settings and fallback owner connection settings. |
| `PORT` | Port supplied by the container platform; defaults to `8000`. |
| `TEST_DATABASE_URL` | Optional isolated PostgreSQL database for integration tests. |

`DATABASE_URL` and `ADMIN_DATABASE_URL` are deliberately separate: application
requests use only the read-only URL, while initialization and loading scripts
use the administrative URL.

## Dashboard screenshots

Add current screenshots in `docs/screenshots/` when available:

![Forecast dashboard screenshot placeholder](docs/screenshots/forecast-dashboard.png)

![Accuracy dashboard screenshot placeholder](docs/screenshots/accuracy-dashboard.png)

## Deploy to Neon and Render

Deployment is documented but has **not** been performed.

1. Create a Neon PostgreSQL database. Keep an owner connection string private
   for initialization and data-loading; use the **pooled connection string for
   the read-only `gridcast_reader` role** as the web service's `DATABASE_URL`.
2. From a trusted local shell, set `ADMIN_DATABASE_URL` to Neon's owner
   connection string, set `APP_DB_USER=gridcast_reader` and a unique
   `APP_DB_PASSWORD`, then run `uv run python scripts/init_db.py`. Load the
   stored data using `ADMIN_DATABASE_URL`; do not give the web service this
   write-capable credential.
3. Create a Render web service from this repository using `render.yaml` (Docker,
   free plan, `/health` health check). Set `DATABASE_URL` to the Neon pooled
   URL for the read-only role. Set `ALLOWED_HOSTS` to include the service's
   `*.onrender.com` hostname and configure `RATE_LIMIT` as needed.
4. Verify `/health`, `/docs`, and each dashboard page. Render's free service may
   sleep between requests; the dashboard shows a waking state and retries.

Neon and Render free-tier limits and features change. **Re-check current
provider limits, pricing, connection limits, and sleep behavior before
deploying.**

The container uses a non-root user and health check. Its Uvicorn command trusts
forwarded headers from any peer (`--forwarded-allow-ips='*'`) because the web
service is intended to sit behind Render's proxy; do not expose the container
directly to untrusted clients.

Weather data by [Open-Meteo.com](https://open-meteo.com/).
