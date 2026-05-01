FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# System deps. Heavier deps (tesseract, libgl) land in Plans 2-3 when needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /usr/local/bin/uv

# Non-root user
RUN useradd --uid 5000 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Resolve deps as root, install into /opt/venv that appuser can read.
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
