"""Warehouse layout and control tables.

Schemas:
  bronze.<table>          raw rows as received, every column as VARCHAR, plus lineage columns
  silver.<table>          typed, deduplicated, contract-checked rows, plus lineage columns
  quarantine.records      rows that failed an error-severity check, with reason codes and the raw record
  control.file_ledger     every source file already loaded, per table (drives incremental loads)
  control.watermarks      high-water mark per table (max partition value loaded)
  control.runs            one row per pipeline run
"""

from __future__ import annotations

from datetime import datetime

import duckdb

from data_engineering.contracts.loader import TableContract

LINEAGE_COLUMNS = {"_source_file": "VARCHAR", "_ingested_at": "TIMESTAMP", "_run_id": "VARCHAR"}
SILVER_EXTRA = {"_content_hash": "VARCHAR", "_quality_warnings": "VARCHAR[]", "_normalized": "VARCHAR[]"}

_DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS control;
CREATE TABLE IF NOT EXISTS quarantine.records (
    run_id VARCHAR, table_name VARCHAR, pk_value VARCHAR, reasons VARCHAR[], details VARCHAR[],
    raw_record JSON, _source_file VARCHAR, _ingested_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS control.file_ledger (
    table_name VARCHAR, source_file VARCHAR, rows_read BIGINT, run_id VARCHAR, loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS control.watermarks (
    table_name VARCHAR PRIMARY KEY, high_water_mark VARCHAR, last_run_id VARCHAR, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS control.runs (
    run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, source VARCHAR,
    tables VARCHAR[], report_path VARCHAR
);
"""


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def init_warehouse(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_DDL)


def table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?", [schema, table]
    ).fetchone()
    return row[0] > 0


def existing_columns(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
        "ORDER BY ordinal_position", [schema, table]
    ).fetchall()
    return [r[0] for r in rows]


def ensure_bronze(con: duckdb.DuckDBPyConnection, table: str, columns: list[str]) -> None:
    """Create bronze.<table> or add columns that appeared in new files (schema evolution is additive)."""
    if not table_exists(con, "bronze", table):
        lineage = ", ".join(f"{ident(c)} {t}" for c, t in LINEAGE_COLUMNS.items())
        con.execute(f"CREATE TABLE bronze.{ident(table)} ({lineage})")
    present = set(existing_columns(con, "bronze", table))
    for col in columns:
        if col not in present:
            con.execute(f"ALTER TABLE bronze.{ident(table)} ADD COLUMN {ident(col)} VARCHAR")


def ensure_silver(con: duckdb.DuckDBPyConnection, contract: TableContract) -> None:
    cols = [f"{ident(c.name)} {c.duckdb_type}" for c in contract.columns]
    cols += [f"{ident(c)} {t}" for c, t in {**SILVER_EXTRA, **LINEAGE_COLUMNS}.items()]
    con.execute(f"CREATE TABLE IF NOT EXISTS silver.{ident(contract.table)} ({', '.join(cols)})")
    present = set(existing_columns(con, "silver", contract.table))
    for col, kind in SILVER_EXTRA.items():  # warehouses created before a lineage column existed
        if col not in present:
            con.execute(f"ALTER TABLE silver.{ident(contract.table)} ADD COLUMN {ident(col)} {kind}")


def loaded_files(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = con.execute("SELECT source_file FROM control.file_ledger WHERE table_name = ?", [table]).fetchall()
    return {r[0] for r in rows}


def record_files(con: duckdb.DuckDBPyConnection, table: str, files: list[tuple[str, int]], run_id: str,
                 loaded_at: datetime) -> None:
    """Add loaded files to the ledger in one statement.

    Values are inlined as escaped SQL literals on purpose: the DuckDB Python client probes for pandas on every bound
    parameter, and without pandas installed each probe is an uncached import lookup (thousands per run).
    """
    if not files:
        return
    rows = ", ".join(f"({literal(table)}, {literal(rel)}, {int(n)}, {literal(run_id)}, "
                     f"TIMESTAMP '{loaded_at:%Y-%m-%d %H:%M:%S.%f}')" for rel, n in files)
    con.execute(f"INSERT INTO control.file_ledger VALUES {rows}")


def get_watermark(con: duckdb.DuckDBPyConnection, table: str) -> str | None:
    row = con.execute("SELECT high_water_mark FROM control.watermarks WHERE table_name = ?", [table]).fetchone()
    return row[0] if row else None


def set_watermark(con: duckdb.DuckDBPyConnection, table: str, value: str | None, run_id: str) -> None:
    con.execute("DELETE FROM control.watermarks WHERE table_name = ?", [table])
    con.execute("INSERT INTO control.watermarks VALUES (?, ?, ?, now())", [table, value, run_id])


def reset_table(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Full refresh of one table: forget everything loaded for it."""
    con.execute(f"DROP TABLE IF EXISTS bronze.{ident(table)}")
    con.execute(f"DROP TABLE IF EXISTS silver.{ident(table)}")
    con.execute("DELETE FROM quarantine.records WHERE table_name = ?", [table])
    con.execute("DELETE FROM control.file_ledger WHERE table_name = ?", [table])
    con.execute("DELETE FROM control.watermarks WHERE table_name = ?", [table])
