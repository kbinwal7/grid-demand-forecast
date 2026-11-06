"""FastAPI app: serves the JSON API (/v1/...) and the one-page dashboard (/).

Run:  uvicorn gridcast.api.main:app --reload
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from gridcast.api.deps import create_read_only_engine
from gridcast.api.routes_api import router as api_router
from gridcast.api.routes_pages import router as pages_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Scripts only from our own origin. Plotly sets inline styles, hence 'unsafe-inline' for styles.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.engine is None and app.state.initialize_engine and os.getenv("DATABASE_URL"):
        app.state.engine = create_read_only_engine()
        app.state.owns_engine = True
    try:
        yield
    finally:
        if app.state.owns_engine:
            app.state.engine.dispose()


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 503: "unavailable"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, exc.detail if isinstance(exc.detail, str) else "Request failed.")


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return _error(422, "validation_error", message)


def create_app(engine: Engine | None = None, *, initialize_engine: bool = True) -> FastAPI:
    app = FastAPI(
        title="GridCast API",
        description="Day-ahead Indian electricity demand forecasts (read-only).",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.initialize_engine = initialize_engine
    app.state.owns_engine = False

    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.include_router(api_router)
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
