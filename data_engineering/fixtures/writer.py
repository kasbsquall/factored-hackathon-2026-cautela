"""Parquet writer for the synthetic fixture.

Layout written (and the layout the pipeline reader expects by default):
  daily tables:    <out>/<table>/year=YYYY/month=MM/day=DD/part-0.parquet   (by the contract partition column)
  snapshot tables: <out>/<table>/snapshot_date=YYYY-MM-DD/part-0.parquet
The Arrow schema comes from the contract, so the fixture conforms by construction. A file may deviate on purpose:
extra keys become extra columns (schema evolution) and a column holding strings is written as a string column
(type drift between files).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from data_engineering.contracts.loader import ColumnContract, TableContract

_ARROW = {"DATE": pa.date32(), "TIMESTAMP": pa.timestamp("us"), "TIME": pa.time64("us"),
          "BOOLEAN": pa.bool_(), "INTEGER": pa.int32()}


def arrow_type(col: ColumnContract) -> pa.DataType:
    if col.duckdb_type == "VARCHAR":
        return pa.string()
    if col.duckdb_type.startswith("DECIMAL"):
        return pa.float64()
    return _ARROW[col.duckdb_type]


def _schema(contract: TableContract, rows: list[dict]) -> pa.Schema:
    fields = []
    for col in contract.columns:
        has_text = any(isinstance(r.get(col.name), str) for r in rows)
        fields.append(pa.field(col.name, pa.string() if has_text else arrow_type(col)))
    extras = sorted({k for r in rows for k in r} - set(contract.column_names))
    for name in extras:
        fields.append(pa.field(name, pa.array([r.get(name) for r in rows]).type))
    return pa.schema(fields)


def partition_path(contract: TableContract, key: date) -> str:
    if contract.partitioning == "daily":
        return f"year={key:%Y}/month={key:%m}/day={key:%d}"
    return f"snapshot_date={key:%Y-%m-%d}"


def write_table(out: Path, contract: TableContract, rows: list[dict], snapshot: date) -> list[str]:
    """Write rows grouped by partition and return the relative file paths written, in sorted order."""
    groups: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        key = row[contract.partition_column] if contract.partitioning == "daily" else snapshot
        groups[key].append(row)
    written = []
    for key in sorted(groups):
        part_rows = groups[key]
        schema = _schema(contract, part_rows)
        table = pa.Table.from_pylist([{f.name: r.get(f.name) for f in schema} for r in part_rows], schema=schema)
        rel = f"{contract.table}/{partition_path(contract, key)}/part-0.parquet"
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, target)
        written.append(rel)
    return written
