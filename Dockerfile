FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev \
    && groupadd --system gridcast \
    && useradd --system --gid gridcast --create-home \
        --home-dir /home/gridcast --shell /usr/sbin/nologin gridcast

USER gridcast

EXPOSE 8000

# '*' trusts forwarded headers from any peer; expose this service only behind
# the Render proxy (or another trusted proxy) so clients cannot spoof them.
CMD ["sh", "-c", "uvicorn gridcast.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3)"
