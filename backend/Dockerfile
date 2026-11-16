# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS runtime
# LibreOffice (DOCX -> PDF) and fonts with Vietnamese coverage. Company fonts (e.g. Aptos) can be
# mounted into /usr/share/fonts/custom; docker/fonts.conf provides fallbacks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-dejavu fonts-liberation fonts-inter fontconfig \
 && rm -rf /var/lib/apt/lists/*
COPY docker/fonts.conf /etc/fonts/local.conf
RUN mkdir -p /usr/share/fonts/custom /data/files && fc-cache -f
RUN groupadd --system app && useradd --system --gid app --home /app app && chown app:app /data/files
COPY --from=deps /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    INFRAOPS_STORAGE_DIR=/data/files \
    HOME=/tmp
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live').status==200 else 1)"
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
