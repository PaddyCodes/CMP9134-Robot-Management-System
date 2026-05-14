# ── Stage 1: base dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git bash-completion less procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── Stage 2: development (hot-reload) ─────────────────────────────────────
FROM base AS development

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ── Stage 3: production ────────────────────────────────────────────────────
FROM base AS production

# Create non-root user for security
RUN addgroup --system gcs && adduser --system --ingroup gcs gcs

COPY . .

RUN chown -R gcs:gcs /app
USER gcs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
    "--workers", "2", "--log-level", "info"]
