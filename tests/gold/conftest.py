"""Gold and analytics fixtures: gold is built on a copy of the session's full fixture warehouse, so the shared
silver warehouse other tests read is never modified."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from data_engineering.gold.contract import load_gold_contracts
from data_engineering.gold.run import run_gold

# The lineage and run columns differ between builds by design; everything else must be identical.
RUN_COLUMNS = ("_gold_run_id", "_source_run_ids")


@pytest.fixture(scope="session")
def gold_contracts():
    return load_gold_contracts()


@pytest.fixture(scope="session")
def gold_db(tmp_path_factory, full_run) -> tuple[dict, Path]:
    _, silver_db = full_run
    work = tmp_path_factory.mktemp("gold")
    db = work / "warehouse.duckdb"
    shutil.copy2(silver_db, db)
    report = run_gold(db, reports_dir=work / "reports")
    return report, db


@pytest.fixture()
def gold_copy(tmp_path, gold_db) -> Path:
    """A private copy for tests that rebuild or corrupt gold."""
    target = tmp_path / "copy.duckdb"
    shutil.copy2(gold_db[1], target)
    return target


def gold_rows(db: Path, table: str, contract, drop: tuple[str, ...] = RUN_COLUMNS) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        cols = [r[0] for r in con.execute(f"DESCRIBE gold.{table}").fetchall() if r[0] not in drop]
        order = ", ".join(contract.primary_key)
        return con.execute(f"SELECT {', '.join(cols)} FROM gold.{table} ORDER BY {order}").fetchall()


def query(db: Path, sql: str, params: list | None = None) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql, params or []).fetchall()


@pytest.fixture()
def ml_results(tmp_path) -> Path:
    """A minimal results.json with the shape ml/evaluate.py writes; values are test inputs, not results."""
    data = {"proposed_system": "sys", "data_version": "test", "split": "test",
            "systems": {"sys": {"summary": {
                "n_cases": 200, "safe_automated_resolution_rate": {"value": 0.5, "ci95": [0.43, 0.57]},
                "automation_attempted_share": 0.625, "unsafe": {"count": 1, "denominator": 200}}}}}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
