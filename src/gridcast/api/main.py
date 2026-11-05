import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gridcast.api.deps import create_read_only_engine
from gridcast.api.routes_api import limiter
from gridcast.api.routes_api import router as api_router
from gridcast.api.routes_pages import router as pages_router

PACKAGE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = PACKAGE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if (
        app.state.engine is None
        and app.state.initialize_engine
        and (os.getenv("DATABASE_URL") or os.getenv("APP_DATABASE_URL"))
    ):
        app.state.engine = create_read_only_engine()
    try:
        yield
    finally:
        if app.state.engine is not None and app.state.dispose_engine:
            app.state.engine.dispose()


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _allowed_hosts() -> list[str]:
    configured = os.getenv("ALLOWED_HOSTS")
    hosts = (
        [host.strip() for host in configured.split(",") if host.strip()]
        if configured
        else ["localhost", "127.0.0.1", "testserver"]
    )
    if "*.onrender.com" not in hosts:
        hosts.append("*.onrender.com")
    return hosts


def create_app(
    engine: Engine | None = None,
    *,
    initialize_engine: bool = True,
    dispose_engine: bool | None = None,
) -> FastAPI:
    app = FastAPI(
        title="GridCast API",
        description="Indian electricity demand forecasting platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.initialize_engine = initialize_engine
    app.state.dispose_engine = (
        engine is None if dispose_engine is None else dispose_engine
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        if (
            request.method == "GET"
            and request.url.path.startswith("/v1/")
            and response.status_code == 200
            and response.headers.get("content-type", "").startswith(
                "application/json"
            )
        ):
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(
                    chunk.encode(response.charset or "utf-8")
                    if isinstance(chunk, str)
                    else chunk
                )
            body = b"".join(chunks)
            etag = f'"{hashlib.sha256(body).hexdigest()}"'
            response.headers["ETag"] = etag
            response.headers.setdefault("Cache-Control", "private, no-cache")
            vary = response.headers.get("Vary", "")
            if "accept-encoding" not in vary.lower():
                response.headers["Vary"] = (
                    f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"
                )
            if request.headers.get("If-None-Match"):
                validators = {
                    item.strip() for item in request.headers["If-None-Match"].split(",")
                }
                if "*" in validators or etag in validators or f"W/{etag}" in validators:
                    headers = dict(response.headers)
                    headers.pop("content-length", None)
                    return Response(status_code=304, headers=headers)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())
    app.include_router(api_router)
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    if exc.status_code == 422:
        code = "validation_error"
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return _error_response(exc.status_code, code, message)


async def _validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    message = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in errors
    )
    return _error_response(422, "validation_error", message)


async def _rate_limit_error(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return _error_response(
        429, "rate_limit_exceeded", "Too many requests; try again later."
    )


app = create_app()
