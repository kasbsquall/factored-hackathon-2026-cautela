# Cautela API, public demo image. Build from the repository root:
#   docker compose -f deploy/docker-compose.behind-proxy.yml build
# The image holds the service code (agent/, api/, ml/), the runtime dependencies from uv.lock and the demo
# warehouse, which is built here from the synthetic fixture (team-generated, seed 42). No organizer data, no
# .env and no trained model artifacts enter the image (deploy/Dockerfile.dockerignore).

FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

# ---- build: dependencies and the demo warehouse -------------------------------------------------------------
FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /src

# Runtime dependencies only: no dev group, and mlflow (experiment tracking, never imported by the service) pruned
# together with the packages only it needs. Hashes from uv.lock are enforced.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --prune mlflow -o /tmp/requirements.txt \
 && uv venv /opt/venv --python /usr/local/bin/python3.12 \
 && uv pip install --python /opt/venv/bin/python --require-hashes --no-deps -r /tmp/requirements.txt \
 && /opt/venv/bin/python -m compileall -q /opt/venv/lib

COPY agent ./agent
COPY api ./api
COPY ml ./ml
COPY data_engineering ./data_engineering
COPY deploy/serve.py ./deploy/serve.py

# Demo warehouse: fixture -> bronze/silver -> gold, in one build step, so gold is never older than silver and the
# tool repository's freshness check passes. Takes about 20 s.
RUN /opt/venv/bin/python -m data_engineering.fixtures.generate --out /tmp/fixture --seed 42 \
 && /opt/venv/bin/python -m data_engineering.pipelines.run --source /tmp/fixture --target /src/data/warehouse.duckdb \
 && /opt/venv/bin/python -m data_engineering.gold.run --target /src/data/warehouse.duckdb \
 && rm -rf /tmp/fixture /src/data/reports \
 && /opt/venv/bin/python -m compileall -q agent api ml deploy

# ---- runtime -------------------------------------------------------------------------------------------------
FROM python:3.12-slim
RUN groupadd --system --gid 10001 cautela \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cautela \
 && mkdir -p /var/lib/cautela && chown 10001:10001 /var/lib/cautela

COPY --from=build /opt/venv /opt/venv
COPY --from=build /src/agent /app/agent
COPY --from=build /src/api /app/api
COPY --from=build /src/ml /app/ml
COPY --from=build /src/deploy/serve.py /app/deploy/serve.py
COPY --from=build /src/data/warehouse.duckdb /app/data/warehouse.duckdb

# Code and warehouse are root-owned and read-only for the service user. The only writable path is the volume at
# /var/lib/cautela (audit JSONL and the LLM budget counters); /tmp is a tmpfs in the compose file.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    CAUTELA_API_HOST=0.0.0.0 \
    CAUTELA_API_PORT=8000 \
    CAUTELA_WAREHOUSE=/app/data/warehouse.duckdb \
    CAUTELA_CASES_DB=:memory: \
    CAUTELA_AUDIT_DIR=/var/lib/cautela/audit \
    LLM_BUDGET_FILE=/var/lib/cautela/llm_budget.json

WORKDIR /app
USER 10001:10001
EXPOSE 8000
CMD ["python", "/app/deploy/serve.py"]
