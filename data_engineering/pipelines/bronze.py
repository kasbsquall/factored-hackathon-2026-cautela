"""Bronze step: land new source files as received, detect schema drift per file.

Every column is stored as VARCHAR. Files of the same table may disagree on types (a column written as a number in
one partition and as text in another), and a text landing zone accepts both without losing the original value.
Typing happens in silver, where a value that does not fit the contract is quarantined with the raw text kept.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import duckdb

from data_engineering.contracts.loader import TableContract
from data_engineering.pipelines.source import SourceFile, SourceLocation, read_relation_sql
from data_engineering.pipelines.warehouse import ensure_bronze, ident

BATCH_TABLE = "batch_raw"


def detect_drift(contract: TableContract, schemas: dict[str, dict[str, str]]) -> list[dict]:
    """Compare each file's columns with the contract. Returns drift events; never raises."""
    expected = set(contract.column_names)
    unexpected: dict[str, list[str]] = defaultdict(list)
    missing: dict[str, list[str]] = defaultdict(list)
    types: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rel_path in sorted(schemas):
        cols = schemas[rel_path]
        for col in sorted(set(cols) - expected):
            unexpected[col].append(rel_path)
        for col in sorted(expected - set(cols)):
            missing[col].append(rel_path)
        for col, sig in cols.items():
            types[col][sig] += 1
    events = []
    for kind, found in (("unexpected_column", unexpected), ("missing_column", missing)):
        for col, files in sorted(found.items()):
            events.append({"kind": kind, "column": col, "files": len(files),
                           "first_file": files[0], "last_file": files[-1]})
    for col, variants in sorted(types.items()):
        if len(variants) > 1:
            events.append({"kind": "type_changed", "column": col, "variants": dict(sorted(variants.items()))})
    return events


def _select_sql(columns: list[str], loc: SourceLocation, run_id: str, ingested_at: datetime) -> str:
    casts = ", ".join(f"CAST({ident(c)} AS VARCHAR) AS {ident(c.lower())}" for c in columns)
    root = loc.root.replace("'", "''")
    source_file = (f"CASE WHEN starts_with(replace(filename, '\\', '/'), '{root}/') "
                   f"THEN substr(replace(filename, '\\', '/'), {len(loc.root) + 2}) "
                   f"ELSE replace(filename, '\\', '/') END")
    return (f"SELECT {casts}, {source_file} AS _source_file, TIMESTAMP '{ingested_at:%Y-%m-%d %H:%M:%S.%f}' "
            f"AS _ingested_at, '{run_id}' AS _run_id")


def load_batch(con: duckdb.DuckDBPyConnection, loc: SourceLocation, contract: TableContract,
               files: list[SourceFile], schemas: dict[str, dict[str, str]], run_id: str,
               ingested_at: datetime) -> list[str]:
    """Read new files into the temp table batch_raw and append them to bronze. Returns the batch columns.

    Files are grouped by identical schema (format, column names and types) and each group is read in one scan.
    Grouping first avoids asking DuckDB to reconcile conflicting types across files, which would abort the
    surrounding transaction; within a group every value is cast to text.
    """
    columns = sorted({c for rel in schemas.values() for c in rel})
    defs = [f"{ident(c)} VARCHAR" for c in columns]
    defs += ["_source_file VARCHAR", "_ingested_at TIMESTAMP", "_run_id VARCHAR", "__rid BIGINT"]
    con.execute(f"DROP TABLE IF EXISTS {BATCH_TABLE}")
    con.execute(f"CREATE TEMP TABLE {BATCH_TABLE} ({', '.join(defs)})")
    groups: dict[tuple, list[SourceFile]] = defaultdict(list)
    for f in files:
        groups[(f.fmt, tuple(sorted(schemas[f.rel_path].items())))].append(f)
    for key in sorted(groups):
        _insert(con, loc, groups[key], schemas, run_id, ingested_at)
    con.execute(f"UPDATE {BATCH_TABLE} SET __rid = rowid")
    ensure_bronze(con, contract.table, columns)
    con.execute(f"INSERT INTO bronze.{ident(contract.table)} BY NAME SELECT * EXCLUDE (__rid) FROM {BATCH_TABLE}")
    return columns


def _insert(con: duckdb.DuckDBPyConnection, loc: SourceLocation, group: list[SourceFile],
            schemas: dict[str, dict[str, str]], run_id: str, ingested_at: datetime) -> None:
    group_cols = sorted({c for f in group for c in schemas[f.rel_path]})
    select = _select_sql(group_cols, loc, run_id, ingested_at)
    con.execute(f"INSERT INTO {BATCH_TABLE} BY NAME {select} FROM {read_relation_sql(group)}")


def rows_per_file(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(con.execute(f"SELECT _source_file, count(*) FROM {BATCH_TABLE} GROUP BY 1").fetchall())
