# Cautela: one-command targets. Windows without make: run the commands after each target by hand.
SEED ?= 42
SOURCE ?= data/fixture
TARGET ?= data/warehouse.duckdb
TARGET_S3 ?= data/warehouse_s3.duckdb

.PHONY: fixture pipeline pipeline-s3 cases test all

fixture:  ## synthetic test fixture (team-generated, not organizer data)
	uv run python -m data_engineering.fixtures.generate --out data/fixture --seed $(SEED)

pipeline:  ## bronze and silver, incremental; SOURCE can be a local path or s3://bucket/prefix
	uv run python -m data_engineering.pipelines.run --source $(SOURCE) --target $(TARGET)

pipeline-s3:  ## real data: source and credentials come from .env (LATAM_BANK_S3_URI, AWS_*)
	uv run python -m data_engineering.pipelines.run --target $(TARGET_S3)

cases:  ## rebuild the held-out dispute cases and check them against the committed manifest
	uv run python -m ml.scenarios.build --verify

test:
	uv run pytest

all: fixture pipeline test

# Gold layer and workflow evidence. REPORT_WAREHOUSE must hold the organizer data: the report is committed.
REPORT_WAREHOUSE ?= $(TARGET_S3)
.PHONY: gold analytics

gold:  ## gold serving and analytics tables from silver, incremental; TARGET selects the warehouse
	uv run python -m data_engineering.gold.run --target $(TARGET)

analytics:  ## data_analytics/reports: why-this-workflow.md and chart JSON, from a warehouse with gold built
	uv run python -m data_analytics.run --warehouse $(REPORT_WAREHOUSE)
