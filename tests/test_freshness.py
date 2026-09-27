"""Update correctness when delivered files change in place, and the freshness tooling. Local fixtures only.

The synthetic test fixture (team-generated, not organizer data) plays the source. A file counts as rewritten when
its size or modification time differs from the ledger's; its old rows are withdrawn, older versions of the withdrawn
keys are replayed from bronze, and gold rebuilds in full. Every scenario is checked against a warehouse built in one
load from the final state of the same landing directory.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from data_engineering.fixtures.generate import generate
from data_engineering.freshness import compare, demo, stage, status
from data_engineering.freshness.report import render
from data_engineering.gold.contract import load_gold_contracts
from data_engineering.gold.run import run_gold
from data_engineering.pipelines.report import _default
from data_engineering.pipelines.run import run_pipeline
from data_engineering.pipelines.warehouse import is_rewritten

from .conftest import scalar, silver_rows
from .gold.conftest import gold_rows

GOLD_TABLES = sorted(t for t, c in load_gold_contracts().items() if c.materialized)


def _rewrite(path: Path, select: str, scratch: Path, params: list | None = None) -> None:
    """Rewrite a Parquet file in place with `select` (reading the file as `src`), one minute newer than before."""
    before = path.stat().st_mtime_ns
    tmp = scratch / "rewrite.parquet"
    with duckdb.connect() as con:
        con.execute(f"CREATE TABLE src AS SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning = false)")
        con.execute(f"COPY ({select}) TO '{tmp.as_posix()}' (FORMAT parquet)", params or [])
    os.replace(tmp, path)
    os.utime(path, ns=(before + 60 * 10**9, before + 60 * 10**9))


def _load(landing: Path, db: Path, reports: Path) -> dict:
    report = run_pipeline(str(landing), db, reports_dir=reports)
    run_gold(db, reports_dir=reports)
    return report


def _all_equal(left: Path, right: Path, contracts) -> dict:
    result = compare.compare_warehouses(left, right, list(contracts), GOLD_TABLES)
    return {k: v for k, v in result.items() if not v["equal"]}


def _single_version_keys(db: Path) -> tuple[str, str, str]:
    """A transactions file and two of its keys that no other file delivers: (file, key, key)."""
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "WITH single AS (SELECT s.transaction_id, s._source_file FROM silver.transactions s JOIN "
            "(SELECT transaction_id FROM bronze.transactions GROUP BY 1 HAVING count(DISTINCT _source_file) = 1) "
            "USING (transaction_id)) "
            "SELECT _source_file, min(transaction_id), max(transaction_id) FROM single "
            "GROUP BY 1 HAVING count(*) >= 2 ORDER BY 1 LIMIT 1").fetchone()


# One partition rewritten: one row changed, one row withdrawn.

@pytest.fixture(scope="module")
def partition_rewrite(tmp_path_factory, fixture_source, contracts):
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("partition_rewrite")
    landing, db, reports = work / "landing", work / "update.duckdb", work / "reports"
    shutil.copytree(source, landing)
    first = _load(landing, db, reports)
    before_db = work / "before.duckdb"
    shutil.copy2(db, before_db)
    rel, changed, dropped = _single_version_keys(db)
    rows_in_file = scalar(db, "SELECT count(*) FROM silver.transactions WHERE _source_file = ?", [rel])
    _rewrite(landing / rel, "SELECT * REPLACE (CASE WHEN transaction_id = ? THEN 'Rewritten merchant' "
                            "ELSE merchant_name END AS merchant_name) FROM src WHERE transaction_id IS DISTINCT FROM ?",
             work, [changed, dropped])
    second = run_pipeline(str(landing), db, reports_dir=reports)
    with duckdb.connect(str(db), read_only=True) as con:
        between = status.snapshot(con, date(2026, 7, 1))
    gold_second = run_gold(db, reports_dir=reports)
    full_db = work / "full.duckdb"
    _load(landing, full_db, reports)
    return {"first": first, "second": second, "gold": gold_second, "db": db, "before_db": before_db,
            "full_db": full_db, "rel": rel, "changed": changed, "dropped": dropped, "rows_in_file": rows_in_file,
            "between": between}


def test_rewritten_partition_is_reloaded_alone(partition_rewrite):
    tables = partition_rewrite["second"]["tables"]
    tx = tables["transactions"]
    assert tx["files"]["rewritten"] == 1 and tx["files"]["new"] == 0
    assert tx["files"]["already_loaded"] == tx["files"]["discovered"] - 1
    assert tx["rows"]["retracted"]["silver_rows"] == partition_rewrite["rows_in_file"]
    assert tx["rows"]["bronze_in"] == tx["rows"]["retracted"]["bronze_rows"] - 1
    for name, sec in tables.items():
        if name != "transactions":
            assert sec["files"]["rewritten"] == 0 and sec["rows"]["bronze_in"] == 0, name


def test_rewritten_partition_converges_to_single_load(partition_rewrite, contracts):
    db, full_db = partition_rewrite["db"], partition_rewrite["full_db"]
    for name, contract in contracts.items():
        assert silver_rows(db, contract) == silver_rows(full_db, contract), name
    assert _all_equal(db, full_db, contracts) == {}


def test_rewrite_changes_and_withdraws_rows(partition_rewrite):
    db = partition_rewrite["db"]
    assert scalar(db, "SELECT merchant_name FROM silver.transactions WHERE transaction_id = ?",
                  [partition_rewrite["changed"]]) == "Rewritten merchant"
    for table in ("bronze.transactions", "silver.transactions", "gold.customer_transactions"):
        assert scalar(db, f"SELECT count(*) FROM {table} WHERE transaction_id = ?",
                      [partition_rewrite["dropped"]]) == 0, table


def test_ledger_keeps_the_superseded_version(partition_rewrite):
    rows = duckdb.connect(str(partition_rewrite["db"]), read_only=True).execute(
        "SELECT superseded_by_run IS NULL, run_id FROM control.file_ledger WHERE source_file = ? ORDER BY loaded_at",
        [partition_rewrite["rel"]]).fetchall()
    second_run = partition_rewrite["second"]["run_id"]
    assert rows == [(False, partition_rewrite["first"]["run_id"]), (True, second_run)]


def test_gold_rebuilds_in_full_after_a_rewrite(partition_rewrite):
    """customer_transactions would otherwise be patched by key, which cannot see the withdrawn transaction."""
    sections = partition_rewrite["gold"]["tables"]
    assert sections["customer_transactions"]["mode"] == "full"
    for name, contract in load_gold_contracts().items():
        if contract.materialized:
            reads_transactions = any(s.split(".")[-1] == "transactions" for s in contract.sources)
            assert sections[name]["mode"] == ("full" if reads_transactions else "skipped"), name
            assert gold_rows(partition_rewrite["db"], name, contract) == \
                gold_rows(partition_rewrite["full_db"], name, contract), name


def test_gold_is_stale_between_the_silver_and_gold_runs(partition_rewrite):
    gold = partition_rewrite["between"]["gold"]
    assert gold["status"] == "stale" and gold["gold_older_than_silver"]
    assert gold["tables"]["customer_transactions"] == "stale"
    with duckdb.connect(str(partition_rewrite["db"]), read_only=True) as con:
        after = status.snapshot(con, date(2026, 7, 1))["gold"]
    assert after["status"] == "fresh" and set(after["tables"].values()) == {"current"}


# A rewritten file whose keys also have an older version in another file.

@pytest.fixture(scope="module")
def replay_rewrite(tmp_path_factory, fixture_source):
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("replay_rewrite")
    landing, db, reports = work / "landing", work / "update.duckdb", work / "reports"
    shutil.copytree(source, landing)
    run_pipeline(str(landing), db, reports_dir=reports)
    with duckdb.connect(str(db), read_only=True) as con:
        # a key with exactly one other version, which passed the checks when it was loaded
        key, winner, older = con.execute(
            "SELECT s.transaction_id, s._source_file, min(b._source_file) FROM silver.transactions s "
            "JOIN bronze.transactions b ON b.transaction_id = s.transaction_id AND b._source_file <> s._source_file "
            "WHERE NOT EXISTS (SELECT 1 FROM quarantine.records q WHERE q.table_name = 'transactions' "
            "AND q.pk_value = s.transaction_id) "
            "GROUP BY ALL HAVING count(DISTINCT b._source_file) = 1 ORDER BY 1 LIMIT 1").fetchone()
        quarantined = con.execute("SELECT count(*) FROM quarantine.records").fetchone()[0]
    _rewrite(landing / winner, "SELECT * FROM src WHERE transaction_id IS DISTINCT FROM ?", work, [key])
    report = run_pipeline(str(landing), db, reports_dir=reports)
    full_db = work / "full.duckdb"
    run_pipeline(str(landing), full_db, reports_dir=reports)
    return {"report": report, "db": db, "full_db": full_db, "key": key, "winner": winner, "older": older,
            "quarantined": quarantined}


def test_withdrawn_winner_brings_back_the_older_version(replay_rewrite):
    tx = replay_rewrite["report"]["tables"]["transactions"]
    assert tx["files"]["rewritten"] == 1
    assert tx["rows"]["replayed"] >= 1
    assert scalar(replay_rewrite["db"], "SELECT _source_file FROM silver.transactions WHERE transaction_id = ?",
                  [replay_rewrite["key"]]) == replay_rewrite["older"]


def test_replay_converges_to_single_load(replay_rewrite, contracts):
    db, full_db = replay_rewrite["db"], replay_rewrite["full_db"]
    for name, contract in contracts.items():
        assert silver_rows(db, contract) == silver_rows(full_db, contract), name
    diff = compare.compare_warehouses(db, full_db, list(contracts), [])
    assert {k: v for k, v in diff.items() if not v["equal"]} == {}


def test_replayed_rows_are_not_written_twice(replay_rewrite):
    """Replayed rows are already in bronze and, when they failed a check, already in quarantine."""
    db, tx = replay_rewrite["db"], replay_rewrite["report"]["tables"]["transactions"]
    assert scalar(db, "SELECT count(*) FROM bronze.transactions") == \
        scalar(replay_rewrite["full_db"], "SELECT count(*) FROM bronze.transactions")
    assert scalar(db, "SELECT count(*) FROM quarantine.records") == \
        replay_rewrite["quarantined"] - tx["rows"]["retracted"]["quarantine_rows"] + tx["rows"]["quarantined"]


# Ledger rows written before the ledger kept fingerprints.

def test_is_rewritten_needs_both_fingerprints():
    assert is_rewritten((10, 1000), (11, 1000))
    assert is_rewritten((10, 1000), (10, 2000))
    assert not is_rewritten((10, 1000), (10, 1000))
    assert not is_rewritten((None, None), (10, 1000))
    assert not is_rewritten((10, 1000), (None, None))


def test_old_ledger_rows_are_treated_as_unchanged(tmp_path, fixture_source, contracts):
    source, _ = fixture_source
    landing, db = tmp_path / "landing", tmp_path / "w.duckdb"
    (landing / "branches").mkdir(parents=True)
    shutil.copytree(source / "branches", landing / "branches", dirs_exist_ok=True)
    run_pipeline(str(landing), db, tables=["branches"], reports_dir=tmp_path / "r")
    before = silver_rows(db, contracts["branches"])
    with duckdb.connect(str(db)) as con:  # the ledger as it was before this change
        for col in ("file_size", "file_modified_ms", "superseded_by_run"):
            con.execute(f"ALTER TABLE control.file_ledger DROP COLUMN {col}")
    rel = next(p for p in landing.rglob("*.parquet"))
    _rewrite(rel, "SELECT * REPLACE ('Rewritten' AS branch_name) FROM src", tmp_path)
    report = run_pipeline(str(landing), db, tables=["branches"], reports_dir=tmp_path / "r")
    sec = report["tables"]["branches"]
    assert sec["files"]["rewritten"] == 0 and sec["files"]["new"] == 0
    assert sec["rows"]["bronze_in"] == 0
    assert silver_rows(db, contracts["branches"]) == before
    assert scalar(db, "SELECT count(*) FROM control.file_ledger WHERE file_size IS NULL") == 1


def test_rewritten_file_with_a_new_column(tmp_path, fixture_source, contracts):
    """The new column is added to bronze before the old rows are withdrawn (DuckDB refuses to commit a transaction
    that deletes from a table and then alters it)."""
    source, _ = fixture_source
    landing, db = tmp_path / "landing", tmp_path / "w.duckdb"
    shutil.copytree(source / "branches", landing / "branches")
    run_pipeline(str(landing), db, tables=["branches"], reports_dir=tmp_path / "r")
    rel = next(p for p in landing.rglob("*.parquet"))
    _rewrite(rel, "SELECT *, 'x' AS extra_column FROM src", tmp_path)
    report = run_pipeline(str(landing), db, tables=["branches"], reports_dir=tmp_path / "r")
    sec = report["tables"]["branches"]
    assert sec["files"]["rewritten"] == 1
    assert [e["kind"] for e in sec["drift_events"]] == ["unexpected_column"]
    assert scalar(db, "SELECT count(*) FROM bronze.branches WHERE extra_column = 'x'") == sec["rows"]["bronze_in"]
    assert scalar(db, "SELECT count(*) FROM bronze.branches") == sec["rows"]["bronze_in"]
    full = tmp_path / "full.duckdb"
    run_pipeline(str(landing), full, tables=["branches"], reports_dir=tmp_path / "r")
    assert silver_rows(db, contracts["branches"]) == silver_rows(full, contracts["branches"])


# Staging a delivery state.

def _write(path: Path, text: str, mtime_s: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime_s, mtime_s))


def test_copy_state_local_to_local(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write(src / "branches.csv", "branch_id\nB1\n", 1_700_000_000)
    _write(src / "transactions/year=2026/month=01/day=01/part-0.csv", "transaction_id\nT1\n", 1_700_000_100)
    _write(src / "transactions/year=2026/month=01/day=02/part-0.csv", "transaction_id\nT2\n", 1_700_000_200)
    _write(dest / "complaints/year=2026/month=01/day=01/part-0.csv", "complaint_id\nC1\n", 1_700_000_300)
    tables = ["branches", "transactions"]

    def copy() -> dict:
        with duckdb.connect() as con:
            return stage.copy_state(con, str(src), tables, dest, workers=2)

    assert copy() == {"files": 3, "bytes": sum(p.stat().st_size for p in src.rglob("*") if p.is_file()),
                      "copied": 3, "copied_bytes": sum(p.stat().st_size for p in src.rglob("*") if p.is_file()),
                      "kept": 0, "removed": 0}
    day1 = "transactions/year=2026/month=01/day=01/part-0.csv"
    assert (dest / day1).read_bytes() == (src / day1).read_bytes()
    assert (dest / day1).stat().st_mtime_ns // 10**6 == (src / day1).stat().st_mtime_ns // 10**6
    assert copy()["copied"] == 0

    _write(src / day1, "transaction_id\nT1b\n", 1_700_000_900)
    (src / "transactions/year=2026/month=01/day=02/part-0.csv").unlink()
    result = copy()
    assert (result["copied"], result["kept"], result["removed"]) == (1, 1, 1)
    assert (dest / day1).read_text(encoding="utf-8") == "transaction_id\nT1b\n"
    assert not (dest / "transactions/year=2026/month=01/day=02").exists()
    assert (dest / "complaints/year=2026/month=01/day=01/part-0.csv").exists()  # other tables are left alone


# Freshness status.

def test_status_snapshot_applies_the_policy(full_run, contracts):
    _, db = full_run
    with duckdb.connect(str(db), read_only=True) as con:
        clock = status.snapshot(con, date(2026, 1, 1))["dataset_clock"]
        on_time = status.snapshot(con, clock)
        late = status.snapshot(con, clock + timedelta(days=40))
    assert on_time["gold"]["status"] == "not_built"
    for name, t in on_time["tables"].items():
        assert t["rows"] > 0, name
        if t["partitioning"] == "daily":
            assert t["lag_days"] == t["lag_vs_dataset_clock_days"], name
            assert t["status"] == ("fresh" if t["lag_days"] <= 1 else "stale"), name
            assert late["tables"][name]["status"] == "stale", name
        elif t["partitioning"] == "full_snapshot":
            assert t["status"] == "no_policy", name
    assert on_time["tables"]["transactions"]["status"] == "fresh"
    assert on_time["tables"]["transactions"]["max_partition"] == clock


def test_status_reports_tables_not_loaded(tmp_path, fixture_source):
    source, _ = fixture_source
    landing, db = tmp_path / "landing", tmp_path / "w.duckdb"
    shutil.copytree(source / "branches", landing / "branches")
    run_pipeline(str(landing), db, tables=["branches"], reports_dir=tmp_path / "r")
    with duckdb.connect(str(db), read_only=True) as con:
        snap = status.snapshot(con, date(2026, 7, 1))
    assert snap["tables"]["transactions"]["status"] == "not_loaded"
    assert snap["tables"]["branches"]["status"] == "no_policy"
    assert snap["dataset_clock"] is None
    assert status.main(["--target", str(db), "--as-of", "2026-07-01"]) == 1
    assert status.main(["--target", str(tmp_path / "absent.duckdb")]) == 1


# The update test end to end: seed 42 as the earlier state, seed 43 as the current one.

@pytest.fixture(scope="module")
def demo_run(tmp_path_factory, fixture_source, contracts):
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("freshness_demo")
    after = work / "seed43"
    generate(after, 43)
    results = demo.run_demo(str(source), str(after), work / "work", list(contracts), today=date(2026, 7, 1))
    return results, work / "work"


def test_demo_converges_to_single_load_of_the_new_state(demo_run, contracts):
    results, work = demo_run
    assert {k: v for k, v in results["equivalence"].items() if not v["equal"]} == {}
    for name, contract in contracts.items():
        assert silver_rows(work / "update.duckdb", contract) == silver_rows(work / "full.duckdb", contract), name
    for name, contract in load_gold_contracts().items():
        if contract.materialized:
            assert gold_rows(work / "update.duckdb", name, contract) == \
                gold_rows(work / "full.duckdb", name, contract), name


def test_demo_update_rereads_every_changed_file(demo_run):
    results, _ = demo_run
    for name, sec in results["run_after"]["tables"].items():
        files, states = sec["files"], results["files"][name]
        assert files["new"] == states["only_after"], name
        assert files["rewritten"] >= states["changed_content"], name  # a new time with the same bytes also counts
        assert files["rewritten"] + files["new"] + files["already_loaded"] == files["discovered"], name
    assert all(v["mode"] in ("full", "view") for v in results["gold_after"].values())


def test_demo_rerun_changes_nothing(demo_run):
    results, _ = demo_run
    totals = results["rerun"]["totals"]
    assert (totals["files_new"], totals["files_rewritten"], totals["bronze_in"]) == (0, 0, 0)
    assert {v["mode"] for v in results["gold_rerun"].values()} == {"skipped", "view"}
    assert results["freshness_after"]["gold"]["status"] == "fresh"


def test_demo_report_renders_from_the_results(demo_run):
    results, _ = demo_run
    text = render(json.loads(json.dumps(results, default=_default)))
    assert text.startswith("# Update and freshness test")
    for heading in ("## The incremental update", "## Gold", "## Freshness before and after",
                    "## Updated warehouse against a single load of the current state", "## Runtime"):
        assert heading in text
    assert f"{len(results['equivalence'])} of {len(results['equivalence'])} compared objects are equal" in text
