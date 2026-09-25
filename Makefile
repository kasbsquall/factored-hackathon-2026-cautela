# Cautela: one-command targets. Windows without make: run the commands after each target by hand.
SEED ?= 42
SOURCE ?= data/fixture
TARGET ?= data/warehouse.duckdb
TARGET_S3 ?= data/warehouse_s3.duckdb

.PHONY: fixture pipeline pipeline-s3 test all

fixture:  ## synthetic test fixture (team-generated, not organizer data)
	uv run python -m data_engineering.fixtures.generate --out data/fixture --seed $(SEED)

pipeline:  ## bronze and silver, incremental; SOURCE can be a local path or s3://bucket/prefix
	uv run python -m data_engineering.pipelines.run --source $(SOURCE) --target $(TARGET)

pipeline-s3:  ## real data: source and credentials come from .env (LATAM_BANK_S3_URI, AWS_*)
	uv run python -m data_engineering.pipelines.run --target $(TARGET_S3)

test:
	uv run pytest

all: fixture pipeline test
