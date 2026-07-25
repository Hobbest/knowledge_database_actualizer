FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY app ./app

RUN pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin actualizer \
    && mkdir -p /data /vault \
    && chown actualizer:actualizer /data /vault

COPY --from=builder /install /usr/local
COPY --chown=actualizer:actualizer app ./app
COPY --chown=actualizer:actualizer frontend ./frontend
COPY --chown=actualizer:actualizer scripts ./scripts
COPY --chown=actualizer:actualizer requirements.txt pyproject.toml README.md LICENSE ./

EXPOSE 8000

VOLUME ["/data", "/vault"]

# BIND_HOST must match uvicorn --host so startup rejects empty API_TOKEN.
# Plugin discovery is disabled by default for networked/Docker profiles.
ENV BIND_HOST=0.0.0.0 \
    ALLOWED_VAULT_ROOTS=/vault \
    DISABLE_PLUGIN_DISCOVERY=true

USER actualizer

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
