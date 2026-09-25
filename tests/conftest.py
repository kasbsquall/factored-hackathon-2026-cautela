"""Shared fixtures. The synthetic test fixture is generated once per session into a temp directory."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from data_engineering.contracts.loader import load_contracts
from data_engineering.fixtures.generate import generate
from data_engineering.pipelines.run import run_pipeline

SEED = 42


@pytest.fixture(scope="session")
def contracts():
    return load_contracts()


@pytest.fixture(scope="session")
def fixture_source(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("fixture") / "source"
    manifest = generate(out, SEED)
    return out, manifest


@pytest.fixture(scope="session")
def full_run(tmp_path_factory, fixture_source) -> tuple[dict, Path]:
    """One full pipeline run over the whole fixture."""
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("full")
    report = run_pipeline(str(source), work / "warehouse.duckdb", reports_dir=work / "reports")
    return report, work / "warehouse.duckdb"


def silver_rows(db: Path, contract, extra: tuple[str, ...] = ("_content_hash", "_source_file")) -> list[tuple]:
    """Silver content in a stable order, without run-specific lineage (_run_id, _ingested_at)."""
    cols = ", ".join(f'"{c}"' for c in [*contract.column_names, *extra])
    order = ", ".join(f'"{k}"' for k in contract.primary_key)
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(f'SELECT {cols} FROM silver."{contract.table}" ORDER BY {order}').fetchall()


def scalar(db: Path, sql: str, params: list | None = None):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql, params or []).fetchone()[0]
