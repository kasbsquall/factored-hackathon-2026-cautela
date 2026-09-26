"""Build the gold tables from silver, incrementally and idempotently.

For each gold table, `control.gold_state` stores the contract's definition hash and, per silver source, the
highest `_ingested_at` and row count seen at the last build ("source marks"). A run compares them with silver now:

  unchanged marks and definition   skipped; a rerun on the same silver changes nothing
  new definition or no state yet   full build (drop, create from the contract, insert)
  row table, changed marks         incremental: the contract's changed_keys queries find the gold keys touched
                                   since the old marks; those keys are deleted and re-inserted, and keys that
                                   left silver are deleted
  row table, unsafe marks          full build, failing closed: a source whose high-water mark did not advance,
                                   that lost rows, or that was reloaded whole (silver full refresh, the only way
                                   silver drops keys, which a secondary source's key query cannot see)
  aggregate, changed marks         full rebuild (small tables whose percentiles cannot be patched)
  view                             recreated every run

Each table is built, checked and committed in one transaction, so a failed check leaves the previous version.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

from data_engineering.gold import checks
from data_engineering.gold.contract import LINEAGE_COLUMNS, GoldContract, build_order
from data_engineering.pipelines.warehouse import ident, literal, table_exists

_DDL = """
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS control;
CREATE TABLE IF NOT EXISTS control.gold_state (
    gold_table VARCHAR PRIMARY KEY, definition_hash VARCHAR, source_marks JSON, last_run_id VARCHAR,
    row_count BIGINT, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS control.gold_runs (
    run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, tables VARCHAR[],
    report_path VARCHAR
);
"""


def init_gold(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_DDL)


def silver_sources(contract: GoldContract) -> list[str]:
    return [s for s in contract.sources if not s.startswith("gold.")]


def source_marks(con: duckdb.DuckDBPyConnection, contract: GoldContract) -> dict[str, dict]:
    """Silver sources are marked by their highest _ingested_at and row count; gold sources by the run that last
    changed them, so a rebuilt gold input (new definition or new rows) also refreshes what reads it."""
    marks = {}
    for source in contract.sources:
        if source.startswith("gold."):
            row = con.execute("SELECT last_run_id FROM control.gold_state WHERE gold_table = ?",
                              [source.removeprefix("gold.")]).fetchone()
            marks[source] = {"last_run_id": row[0] if row else None}
    for name in silver_sources(contract):
        if not table_exists(con, "silver", name):
            raise ValueError(f"silver.{name} does not exist; run the bronze/silver pipeline first")
        lo, hi, rows = con.execute(f"SELECT min(_ingested_at), max(_ingested_at), count(*) "
                                   f"FROM silver.{ident(name)}").fetchone()
        marks[name] = {"min_ingested_at": lo.isoformat() if lo else None,
                       "max_ingested_at": hi.isoformat() if hi else None, "rows": rows}
    return marks


def _state(con: duckdb.DuckDBPyConnection, table: str) -> dict | None:
    row = con.execute("SELECT definition_hash, source_marks FROM control.gold_state WHERE gold_table = ?",
                      [table]).fetchone()
    return {"definition_hash": row[0], "source_marks": json.loads(row[1])} if row else None


def _unsafe_for_increment(old: dict, new: dict) -> bool:
    """True when a changed silver source cannot be patched by key: its watermark did not advance (the changed
    rows would be invisible to `_ingested_at > wm`), it lost rows, or every row was reloaded."""
    if old == new:
        return False
    if not (old.get("max_ingested_at") and new.get("max_ingested_at") and new.get("min_ingested_at")):
        return True
    old_hi = datetime.fromisoformat(old["max_ingested_at"])
    new_lo, new_hi = datetime.fromisoformat(new["min_ingested_at"]), datetime.fromisoformat(new["max_ingested_at"])
    return new_hi <= old_hi or new_lo > old_hi or new["rows"] < old["rows"]


def plan_mode(con: duckdb.DuckDBPyConnection, contract: GoldContract, marks: dict) -> str:
    if not contract.materialized:
        return "view"
    state = _state(con, contract.table)
    if (state is None or state["definition_hash"] != contract.definition_hash
            or not table_exists(con, "gold", contract.table)):
        return "full"
    if state["source_marks"] == marks:
        return "skipped"
    if contract.kind == "aggregate":
        return "full"
    old = state["source_marks"]
    if any(_unsafe_for_increment(old.get(s, {}), marks[s]) for s in silver_sources(contract)):
        return "full"
    if any(old.get(s) != marks[s] for s in marks if s.startswith("gold.")):
        return "full"  # row tables have no key queries for gold inputs
    return "incremental"


def _source_table(contract: GoldContract) -> str:
    src = contract.lineage_source
    return src if src.startswith("gold.") else f"silver.{src}"


def _select_with_lineage(contract: GoldContract, run_id: str) -> str:
    return (f"SELECT q.*, {literal(_source_table(contract))} AS _source_table, {literal(run_id)} AS _gold_run_id "
            f"FROM ({contract.sql}) AS q")


def _create_table(con: duckdb.DuckDBPyConnection, contract: GoldContract) -> None:
    cols = [f"{ident(c.name)} {c.type}" for c in contract.columns]
    cols += [f"{ident(c)} {t}" for c, t in LINEAGE_COLUMNS.items()]
    con.execute(f"DROP TABLE IF EXISTS gold.{ident(contract.table)}")
    con.execute(f"CREATE TABLE gold.{ident(contract.table)} ({', '.join(cols)})")


def _full(con: duckdb.DuckDBPyConnection, contract: GoldContract, run_id: str) -> dict:
    _create_table(con, contract)
    inserted = con.execute(f"INSERT INTO gold.{ident(contract.table)} BY NAME "
                           f"{_select_with_lineage(contract, run_id)}").fetchone()[0]
    return {"keys_changed": None, "rows_deleted": None, "rows_inserted": inserted}


def _incremental(con: duckdb.DuckDBPyConnection, contract: GoldContract, run_id: str, old: dict,
                 marks: dict) -> dict:
    t = f"gold.{ident(contract.table)}"
    key = ident(contract.primary_key[0])
    parts = []
    for source, sql in contract.changed_keys.items():
        if old[source] == marks[source]:
            continue
        wm = f"TIMESTAMP {literal(old[source]['max_ingested_at'])}"
        parts.append(f"SELECT * FROM ({sql.replace('{wm}', wm)}) AS c(k)")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _gold_changed AS SELECT DISTINCT k FROM ({' UNION ALL '.join(parts)})")
    changed = con.execute("SELECT count(*) FROM _gold_changed").fetchone()[0]
    deleted = con.execute(f"DELETE FROM {t} WHERE {key} IN (SELECT k FROM _gold_changed)").fetchone()[0]
    parent = f"silver.{ident(contract.lineage_source)}"
    deleted += con.execute(f"DELETE FROM {t} AS g WHERE NOT EXISTS "
                           f"(SELECT 1 FROM {parent} AS p WHERE p.{key} = g.{key})").fetchone()[0]
    inserted = con.execute(f"INSERT INTO {t} BY NAME SELECT * FROM ({_select_with_lineage(contract, run_id)}) AS s "
                           f"WHERE s.{key} IN (SELECT k FROM _gold_changed)").fetchone()[0]
    con.execute("DROP TABLE IF EXISTS _gold_changed")
    return {"keys_changed": changed, "rows_deleted": deleted, "rows_inserted": inserted}


def build_table(con: duckdb.DuckDBPyConnection, contract: GoldContract, run_id: str) -> dict:
    """Build, check and commit one gold table. Returns its report section."""
    marks = source_marks(con, contract)
    mode = plan_mode(con, contract, marks)
    section = {"kind": contract.kind, "purpose": contract.purpose, "mode": mode, "source_marks": marks}
    con.execute("BEGIN TRANSACTION")
    try:
        problems = checks.schema_mismatches(con, contract)
        if problems:
            raise checks.GoldQualityError(contract.table, [{"check": "schema", "column": p, "violations": 1}
                                                          for p in problems])
        if mode == "view":
            con.execute(f"CREATE OR REPLACE VIEW gold.{ident(contract.table)} AS {contract.sql}")
            section.update(keys_changed=None, rows_deleted=None, rows_inserted=None)
        elif mode == "full":
            section.update(_full(con, contract, run_id))
        elif mode == "incremental":
            section.update(_incremental(con, contract, run_id, _state(con, contract.table)["source_marks"], marks))
        else:
            section.update(keys_changed=0, rows_deleted=0, rows_inserted=0)
        results = checks.run_checks(con, contract)
        checks.enforce(contract, results)
        total = con.execute(f"SELECT count(*) FROM gold.{ident(contract.table)}").fetchone()[0]
        if mode != "skipped":
            con.execute("DELETE FROM control.gold_state WHERE gold_table = ?", [contract.table])
            con.execute("INSERT INTO control.gold_state VALUES (?, ?, ?, ?, ?, now())",
                        [contract.table, contract.definition_hash, json.dumps(marks, sort_keys=True), run_id, total])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    section.update(rows_total=total, checks=results)
    return section


def build_gold(con: duckdb.DuckDBPyConnection, contracts: dict[str, GoldContract], run_id: str,
               tables: list[str] | None = None) -> dict[str, dict]:
    """Build the selected gold tables (default all) in dependency order."""
    init_gold(con)
    order = build_order(contracts)
    if tables:
        unknown = sorted(set(tables) - set(contracts))
        if unknown:
            raise ValueError(f"unknown gold tables: {unknown}")
        order = [t for t in order if t in tables]
    return {name: build_table(con, contracts[name], run_id) for name in order}
