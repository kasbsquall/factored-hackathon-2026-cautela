# Cautela API, public demo image. Build from the repository root:
#   docker compose -f deploy/docker-compose.behind-proxy.yml build
#
# Two data sources, chosen with the build argument CAUTELA_DATA:
#   bundle  (default) the demo bundle deploy/demo-bundle/ (git-ignored, built by `make demo-artifacts` and shipped
#           next to the release tarball): the learned disposition model, a slim warehouse with the demo customers of
#           the organizer dataset, and their seed. The build fails unless the bundle matches the committed
#           deploy/demo-bundle.lock.json byte for byte, and deploy/serve.py checks it again at every start.
#   fixture the synthetic fixture (team-generated, seed 42) taken through bronze, silver and gold during the build,
#           with the labeled rule baseline. No organizer data and no model: for CI and local checks.
# No .env, no data/ and no other organizer file enters the build context (deploy/Dockerfile.dockerignore).

ARG CAUTELA_DATA=bundle

FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

# ---- build: dependencies -------------------------------------------------------------------------------------
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
COPY deploy/serve.py deploy/lock.py ./deploy/
# Read at runtime: every handoff is validated against it before it reaches the console.
COPY docs/schemas/handoff.schema.json ./docs/schemas/handoff.schema.json
RUN /opt/venv/bin/python -m compileall -q agent api ml deploy

# ---- data, variant 1: the demo bundle, verified against the committed lock ------------------------------------
FROM build AS data-bundle
COPY deploy/demo-bundle.lock.json ./deploy/demo-bundle.lock.json
COPY deploy/demo-bundle ./bundle
RUN cd /src && /opt/venv/bin/python -m deploy.lock verify /src/bundle /src/deploy/demo-bundle.lock.json \
 && mkdir -p /out && mv /src/bundle /out/bundle && cp /src/deploy/demo-bundle.lock.json /out/demo-bundle.lock.json

# ---- data, variant 2: the synthetic fixture, bronze -> silver -> gold in one step (gold never older than silver) --
FROM build AS data-fixture
COPY data_engineering ./data_engineering
RUN /opt/venv/bin/python -m data_engineering.fixtures.generate --out /tmp/fixture --seed 42 \
 && /opt/venv/bin/python -m data_engineering.pipelines.run --source /tmp/fixture --target /out/warehouse.duckdb \
 && /opt/venv/bin/python -m data_engineering.gold.run --target /out/warehouse.duckdb \
 && rm -rf /tmp/fixture /src/data/reports /out/reports

# ---- runtime -------------------------------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN groupadd --system --gid 10001 cautela \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cautela \
 && mkdir -p /var/lib/cautela && chown 10001:10001 /var/lib/cautela

COPY --from=build /opt/venv /opt/venv
COPY --from=build /src/agent /app/agent
COPY --from=build /src/api /app/api
COPY --from=build /src/ml /app/ml
COPY --from=build /src/deploy /app/deploy
COPY --from=build /src/docs /app/docs

# Code and data are root-owned and read-only for the service user. The only writable path is the volume at
# /var/lib/cautela (audit JSONL and the LLM budget counters); /tmp is a tmpfs in the compose file.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    CAUTELA_API_HOST=0.0.0.0 \
    CAUTELA_API_PORT=8000 \
    CAUTELA_CASES_DB=:memory: \
    CAUTELA_AUDIT_DIR=/var/lib/cautela/audit \
    LLM_BUDGET_FILE=/var/lib/cautela/llm_budget.json

WORKDIR /app
USER 10001:10001
EXPOSE 8000
CMD ["python", "/app/deploy/serve.py"]

FROM runtime AS runtime-bundle
COPY --from=data-bundle /out/bundle /app/bundle
COPY --from=data-bundle /out/demo-bundle.lock.json /app/deploy/demo-bundle.lock.json
ENV CAUTELA_BUNDLE_DIR=/app/bundle \
    CAUTELA_BUNDLE_LOCK=/app/deploy/demo-bundle.lock.json

FROM runtime AS runtime-fixture
COPY --from=data-fixture /out/warehouse.duckdb /app/data/warehouse.duckdb
ENV CAUTELA_WAREHOUSE=/app/data/warehouse.duckdb

FROM runtime-${CAUTELA_DATA}
