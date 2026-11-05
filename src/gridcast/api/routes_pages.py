from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import select_autoescape

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.autoescape = select_autoescape(
    enabled_extensions=("html", "htm", "xml"),
    default_for_string=True,
)


def _page_context(request: Request, active_page: str) -> dict:
    return {
        "active_page": active_page,
        "csp_nonce": request.state.csp_nonce,
    }


@router.get("/", response_class=HTMLResponse)
@router.get("/forecast", response_class=HTMLResponse)
def forecast_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="forecast.html",
        context=_page_context(request, "forecast"),
    )


@router.get("/accuracy", response_class=HTMLResponse)
def accuracy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="accuracy.html",
        context=_page_context(request, "accuracy"),
    )


@router.get("/failures", response_class=HTMLResponse)
def failures_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="failures.html",
        context=_page_context(request, "failures"),
    )


@router.get("/errors", include_in_schema=False)
def errors_compatibility_redirect():
    return RedirectResponse("/failures", status_code=307)


@router.get("/models", response_class=HTMLResponse)
def models_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="models.html",
        context=_page_context(request, "models"),
    )


@router.get("/data-health", response_class=HTMLResponse)
def data_health_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="data_health.html",
        context=_page_context(request, "data-health"),
    )
