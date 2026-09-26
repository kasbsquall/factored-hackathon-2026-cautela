"""Silver step, part 1: normalize, cast to contract types and evaluate every contract check per row.

Each failed check produces a tag "<column>:<reason>". Tags from error-severity checks go to __errors (the row is
quarantined); tags from warn-severity checks go to __warnings (the row continues to silver and the tags are kept
in its _quality_warnings column).

Reason codes:
  null_pk                  a primary key column is null (always an error)
  type_cast_error          a non-empty value does not fit the contract type (always an error)
  null_required            a NOT NULL column is null
  bad_enum                 the value is outside the dictionary list and the explicitly observed values
  out_of_range             the value is outside the dictionary's stated range
  orphan_fk                the key was never delivered in the parent table (absent from the parent's bronze)
  parent_quarantined       the parent key was delivered but its row is in quarantine (always a warning, so a
                           parent's quality problem never cascades into its children)
  partition_path_mismatch  the partition column disagrees with the file's year=/month=/day= folders (warning)

Normalization happens before the checks: a raw value listed in the column's `normalize` map is replaced by its
canonical form, and the raw value is recorded in __normalized ("column:raw value").
"""

from __future__ import annotations

import duckdb

from data_engineering.contracts.loader import ColumnContract, TableContract
from data_engineering.pipelines.bronze import BATCH_TABLE
from data_engineering.pipelines.warehouse import existing_columns, ident, table_exists

CHECKED_TABLE = "checked"
_PATH_PART = r"regexp_extract(t._source_file, '(^|/){key}=(\d+)/', 2)"


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _raw(col: ColumnContract, batch_columns: set[str]) -> str:
    if col.name not in batch_columns:
        return "CAST(NULL AS VARCHAR)"
    return f"CASE WHEN trim(b.{ident(col.name)}) = '' THEN NULL ELSE b.{ident(col.name)} END"


def _normalized(col: ColumnContract) -> str:
    raw = ident(f"__raw__{col.name}")
    if not col.normalize:
        return raw
    cases = " ".join(f"WHEN {_lit(k)} THEN {_lit(v)}" for k, v in sorted(col.normalize.items()))
    return f"CASE trim({raw}) {cases} ELSE {raw} END"


def _typed(col: ColumnContract) -> str:
    value = _normalized(col)
    if col.duckdb_type == "VARCHAR":
        return value
    return f"TRY_CAST(trim({value}) AS {col.duckdb_type})"


def prepare_parent_keys(con: duckdb.DuckDBPyConnection, contract: TableContract) -> dict[str, str | None]:
    """One temp key table per foreign key: every key the parent delivered (bronze) and whether it reached silver.

    None means the parent was never loaded, so the check is skipped and reported as such.
    """
    fk_tables: dict[str, str | None] = {}
    for col in contract.foreign_keys:
        parent, key = col.fk_table, col.fk_column
        loaded = (table_exists(con, "bronze", parent) and table_exists(con, "silver", parent)
                  and key in existing_columns(con, "bronze", parent))
        if not loaded:
            fk_tables[col.name] = None
            continue
        name = f"__fk_{col.name}"
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS "
            f"SELECT d.k, s.k IS NOT NULL AS in_silver "
            f"FROM (SELECT DISTINCT {ident(key)} AS k FROM bronze.{ident(parent)} WHERE {ident(key)} IS NOT NULL) d "
            f"LEFT JOIN (SELECT DISTINCT CAST({ident(key)} AS VARCHAR) AS k FROM silver.{ident(parent)}) s "
            f"ON s.k = d.k"
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
        values = ", ".join(_lit(v) for v in col.accepted_values)
        checks.append((col.severity_for("bad_enum"), f"t.{name} NOT IN ({values})", "bad_enum"))
    bounds = [f"t.{name} < {col.min}" if col.min is not None else None,
              f"t.{name} > {col.max}" if col.max is not None else None]
    bounds = [b for b in bounds if b]
    if bounds:
        checks.append((col.severity_for("out_of_range"), " OR ".join(bounds), "out_of_range"))
    if fk_alias:
        checks.append((col.severity_for("orphan_fk"), f"t.{name} IS NOT NULL AND {fk_alias}.k IS NULL", "orphan_fk"))
        checks.append(("warn", f"{fk_alias}.k IS NOT NULL AND NOT {fk_alias}.in_silver", "parent_quarantined"))
    return checks


def _partition_check(contract: TableContract) -> tuple[str, str, str] | None:
    """Daily tables stored under year=/month=/day= folders: the folder date must equal the partition column."""
    part = contract.partition_column
    if contract.partitioning != "daily" or part is None:
        return None
    y, m, d = (f"TRY_CAST({_PATH_PART.format(key=k)} AS INTEGER)" for k in ("year", "month", "day"))
    path_date = f"CASE WHEN {y} IS NOT NULL AND {m} IS NOT NULL AND {d} IS NOT NULL THEN make_date({y}, {m}, {d}) END"
    return "warn", f"t.{ident(part)} IS NOT NULL AND {path_date} <> t.{ident(part)}", "partition_path_mismatch"


def _tag_list(items: list[str]) -> str:
    if not items:
        return "CAST([] AS VARCHAR[])"
    return f"list_filter([{', '.join(items)}], x -> x IS NOT NULL)"


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
    partition = _partition_check(contract)
    if partition:
        severity, condition, reason = partition
        tags[severity].append(f"CASE WHEN {condition} THEN {_lit(contract.partition_column + ':' + reason)} END")
    normalized = [f"CASE WHEN trim(t.{ident('__raw__' + c.name)}) IN ({', '.join(_lit(k) for k in sorted(c.normalize))}) "
                  f"THEN {_lit(c.name + ':')} || trim(t.{ident('__raw__' + c.name)}) END"
                  for c in contract.columns if c.normalize]
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {CHECKED_TABLE} AS
        WITH raw AS (SELECT b.__rid, b.__replay, b._source_file, b._ingested_at, b._run_id, {raw_cols}
                     FROM {BATCH_TABLE} b),
        typed AS (SELECT *, {typed_cols} FROM raw)
        SELECT t.*, {_tag_list(tags['error'])} AS __errors, {_tag_list(tags['warn'])} AS __warnings,
               {_tag_list(normalized)} AS __normalized
        FROM typed t {' '.join(joins)}
    """)


def pk_value_sql(contract: TableContract, alias: str = "c") -> str:
    parts = [f"CAST({alias}.{ident(k)} AS VARCHAR)" for k in contract.primary_key]
    return parts[0] if len(parts) == 1 else f"concat_ws('|', {', '.join(parts)})"


def quarantine_failed(con: duckdb.DuckDBPyConnection, contract: TableContract, run_id: str) -> int:
    """Move rows with at least one error tag to quarantine.records, keeping the raw record as received.

    Replayed rows (see bronze.append_replay) were quarantined when their file was first loaded, so they are skipped.
    """
    con.execute(f"""
        INSERT INTO quarantine.records
        SELECT {_lit(run_id)}, {_lit(contract.table)}, {pk_value_sql(contract)},
               list_sort(list_distinct(list_transform(c.__errors, x -> split_part(x, ':', 2)))),
               c.__errors, to_json(b), c._source_file, c._ingested_at
        FROM {CHECKED_TABLE} c JOIN (SELECT * EXCLUDE (__replay) FROM {BATCH_TABLE}) b ON b.__rid = c.__rid
        WHERE len(c.__errors) > 0 AND NOT c.__replay
    """)
    return con.execute(f"SELECT count(*) FROM {CHECKED_TABLE} WHERE len(__errors) > 0 "
                       "AND NOT __replay").fetchone()[0]
