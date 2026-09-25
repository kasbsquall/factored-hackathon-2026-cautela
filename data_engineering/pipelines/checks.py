"""Silver step, part 1: cast the batch to contract types and evaluate every contract check per row.

Each failed check produces a tag "<column>:<reason>". Tags from error-severity checks go to __errors (the row is
quarantined); tags from warn-severity checks go to __warnings (the row continues to silver and the tags are kept
in its _quality_warnings column).

Reason codes:
  null_pk          a primary key column is null (always an error)
  type_cast_error  a non-empty value does not fit the contract type (always an error)
  null_required    a NOT NULL column is null
  bad_enum         the value is outside the dictionary's value list
  out_of_range     the value is outside the dictionary's stated range
  orphan_fk        the key was never delivered in the parent table (neither in silver nor in quarantine)
"""

from __future__ import annotations

import duckdb

from data_engineering.contracts.loader import ColumnContract, TableContract
from data_engineering.pipelines.bronze import BATCH_TABLE
from data_engineering.pipelines.warehouse import ident, table_exists

CHECKED_TABLE = "checked"


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _raw(col: ColumnContract, batch_columns: set[str]) -> str:
    if col.name not in batch_columns:
        return "CAST(NULL AS VARCHAR)"
    return f"CASE WHEN trim(b.{ident(col.name)}) = '' THEN NULL ELSE b.{ident(col.name)} END"


def _typed(col: ColumnContract) -> str:
    raw = ident(f"__raw__{col.name}")
    if col.duckdb_type == "VARCHAR":
        return raw
    return f"TRY_CAST(trim({raw}) AS {col.duckdb_type})"


def prepare_parent_keys(con: duckdb.DuckDBPyConnection, contract: TableContract) -> dict[str, str | None]:
    """Build one temp key table per foreign key. None means the parent is not loaded, so the check is skipped."""
    fk_tables: dict[str, str | None] = {}
    for col in contract.foreign_keys:
        if not table_exists(con, "silver", col.fk_table):
            fk_tables[col.name] = None
            continue
        name = f"__fk_{col.name}"
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS "
            f"SELECT CAST({ident(col.fk_column)} AS VARCHAR) AS k FROM silver.{ident(col.fk_table)} "
            f"UNION SELECT pk_value FROM quarantine.records WHERE table_name = {_lit(col.fk_table)} "
            f"AND pk_value IS NOT NULL"
        )
        fk_tables[col.name] = name
    return fk_tables


def _column_checks(col: ColumnContract, fk_alias: str | None) -> list[tuple[str, str, str]]:
    """(severity, sql condition, reason) triples for one column. Conditions refer to typed alias t."""
    name, raw = ident(col.name), ident(f"__raw__{col.name}")
    checks = []
    if col.duckdb_type != "VARCHAR":
        checks.append(("error", f"t.{raw} IS NOT NULL AND t.{name} IS NULL", "type_cast_error"))
    if col.pk:
        checks.append(("error", f"t.{raw} IS NULL", "null_pk"))
    elif not col.nullable:
        checks.append((col.severity_for("not_null"), f"t.{raw} IS NULL", "null_required"))
    if col.allowed_values:
        values = ", ".join(_lit(v) for v in col.allowed_values)
        checks.append((col.severity_for("bad_enum"), f"t.{name} NOT IN ({values})", "bad_enum"))
    bounds = [f"t.{name} < {col.min}" if col.min is not None else None,
              f"t.{name} > {col.max}" if col.max is not None else None]
    bounds = [b for b in bounds if b]
    if bounds:
        checks.append((col.severity_for("out_of_range"), " OR ".join(bounds), "out_of_range"))
    if fk_alias:
        checks.append((col.severity_for("orphan_fk"), f"t.{name} IS NOT NULL AND {fk_alias}.k IS NULL", "orphan_fk"))
    return checks


def build_checked(con: duckdb.DuckDBPyConnection, contract: TableContract, batch_columns: list[str],
                  fk_tables: dict[str, str | None]) -> None:
    present = set(batch_columns)
    raw_cols = ", ".join(f"{_raw(c, present)} AS {ident('__raw__' + c.name)}" for c in contract.columns)
    typed_cols = ", ".join(f"{_typed(c)} AS {ident(c.name)}" for c in contract.columns)
    joins, tags = [], {"error": [], "warn": []}
    for i, col in enumerate(contract.columns):
        alias = None
        if fk_tables.get(col.name):
            alias = f"fk{i}"
            joins.append(f"LEFT JOIN {fk_tables[col.name]} {alias} ON {alias}.k = t.{ident(col.name)}")
        for severity, condition, reason in _column_checks(col, alias):
            tags[severity].append(f"CASE WHEN {condition} THEN {_lit(col.name + ':' + reason)} END")

    def tag_list(items: list[str]) -> str:
        if not items:
            return "CAST([] AS VARCHAR[])"
        return f"list_filter([{', '.join(items)}], x -> x IS NOT NULL)"

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {CHECKED_TABLE} AS
        WITH raw AS (SELECT b.__rid, b._source_file, b._ingested_at, b._run_id, {raw_cols} FROM {BATCH_TABLE} b),
        typed AS (SELECT *, {typed_cols} FROM raw)
        SELECT t.*, {tag_list(tags['error'])} AS __errors, {tag_list(tags['warn'])} AS __warnings
        FROM typed t {' '.join(joins)}
    """)


def pk_value_sql(contract: TableContract, alias: str = "c") -> str:
    parts = [f"CAST({alias}.{ident(k)} AS VARCHAR)" for k in contract.primary_key]
    return parts[0] if len(parts) == 1 else f"concat_ws('|', {', '.join(parts)})"


def quarantine_failed(con: duckdb.DuckDBPyConnection, contract: TableContract, run_id: str) -> int:
    """Move rows with at least one error tag to quarantine.records, keeping the raw record as received."""
    con.execute(f"""
        INSERT INTO quarantine.records
        SELECT {_lit(run_id)}, {_lit(contract.table)}, {pk_value_sql(contract)},
               list_sort(list_distinct(list_transform(c.__errors, x -> split_part(x, ':', 2)))),
               c.__errors, to_json(b), c._source_file, c._ingested_at
        FROM {CHECKED_TABLE} c JOIN {BATCH_TABLE} b ON b.__rid = c.__rid
        WHERE len(c.__errors) > 0
    """)
    return con.execute(f"SELECT count(*) FROM {CHECKED_TABLE} WHERE len(__errors) > 0").fetchone()[0]
