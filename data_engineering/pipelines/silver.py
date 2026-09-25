"""Silver step, part 2: deduplicate valid rows and upsert them into silver.<table>, then measure the batch.

Deduplication keeps one row per primary key, the latest by the contract's dedupe_order (for daily facts that is
process_date, so a corrected version that lands in a later partition wins). Ties fall back to the source file
path and then the content hash, so the result never depends on read order. The upsert only touches keys present
in the batch, which is what makes reruns idempotent and late arrivals correct.
"""

from __future__ import annotations

import duckdb

from data_engineering.contracts.loader import TableContract
from data_engineering.pipelines.checks import CHECKED_TABLE
from data_engineering.pipelines.warehouse import ident

LATE_ARRIVAL_THRESHOLD_DAYS = 1  # assumption: normal delivery lag is same day or next day
PROFILE_TOP_N = 20


def _pk_join(contract: TableContract, left: str, right: str) -> str:
    return " AND ".join(f"{left}.{ident(k)} = {right}.{ident(k)}" for k in contract.primary_key)


def _pk_cols(contract: TableContract, alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(prefix + ident(k) for k in contract.primary_key)


def build_candidates(con: duckdb.DuckDBPyConnection, contract: TableContract) -> int:
    """Valid rows with a content hash. The partition column is excluded from the hash, so the same record
    re-delivered in another partition counts as an exact duplicate, not as a new version."""
    cols = ", ".join(ident(c) for c in contract.column_names)
    hashed = [c for c in contract.column_names if c != contract.partition_column]
    struct = ", ".join(f"{ident(c)} := {ident(c)}" for c in hashed)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE candidates AS
        SELECT {cols}, md5(CAST(to_json(struct_pack({struct})) AS VARCHAR)) AS _content_hash,
               list_sort(__warnings) AS _quality_warnings, _source_file, _ingested_at, _run_id
        FROM {CHECKED_TABLE} WHERE len(__errors) = 0
    """)
    return con.execute("SELECT count(*) FROM candidates").fetchone()[0]


def merge(con: duckdb.DuckDBPyConnection, contract: TableContract) -> dict[str, int]:
    table, pk = f"silver.{ident(contract.table)}", _pk_cols(contract)
    order = [f"{ident(c)} DESC NULLS LAST" for c in contract.dedupe_order]
    order += ["_source_file DESC", "_content_hash DESC"]
    con.execute(f"CREATE OR REPLACE TEMP TABLE keys AS SELECT DISTINCT {pk} FROM candidates")
    con.execute(f"CREATE OR REPLACE TEMP TABLE existing AS "
                f"SELECT s.* FROM {table} s JOIN keys k ON {_pk_join(contract, 's', 'k')}")
    con.execute("CREATE OR REPLACE TEMP TABLE pool AS "
                "SELECT * FROM existing UNION ALL BY NAME SELECT * FROM candidates")
    con.execute(f"CREATE OR REPLACE TEMP TABLE winners AS SELECT * FROM pool "
                f"QUALIFY row_number() OVER (PARTITION BY {pk} ORDER BY {', '.join(order)}) = 1")
    n_candidates = con.execute("SELECT count(*) FROM candidates").fetchone()[0]
    new_versions = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {pk}, _content_hash FROM candidates "
                               f"EXCEPT SELECT {pk}, _content_hash FROM existing)").fetchone()[0]
    group = ", ".join([pk, *[ident(c) for c in contract.dedupe_order]])
    ambiguous = con.execute(f"SELECT count(*) FROM (SELECT {group} FROM pool GROUP BY {group} "
                            f"HAVING count(DISTINCT _content_hash) > 1)").fetchone()[0]
    inserted = con.execute(f"SELECT count(*) FROM winners w ANTI JOIN existing e "
                           f"ON {_pk_join(contract, 'w', 'e')}").fetchone()[0]
    updated = con.execute(f"SELECT count(*) FROM winners w JOIN existing e ON {_pk_join(contract, 'w', 'e')} "
                          f"WHERE w._content_hash <> e._content_hash").fetchone()[0]
    con.execute(f"DELETE FROM {table} USING keys k WHERE {_pk_join(contract, table, 'k')}")
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM winners")
    return {
        "valid": n_candidates, "exact_duplicates": n_candidates - new_versions,
        "superseded_versions": new_versions - inserted - updated, "ambiguous_versions": ambiguous,
        "inserted": inserted, "updated": updated,
        "unchanged": con.execute("SELECT count(*) FROM keys").fetchone()[0] - inserted - updated,
    }


def _counts(con: duckdb.DuckDBPyConnection, where: str, column: str) -> dict[str, int]:
    rows = con.execute(f"SELECT tag, count(*) FROM (SELECT unnest({column}) AS tag FROM {CHECKED_TABLE} "
                       f"WHERE {where}) GROUP BY tag ORDER BY tag").fetchall()
    return dict(rows)


def _by_reason(by_column: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for tag, n in by_column.items():
        reason = tag.split(":", 1)[1]
        out[reason] = out.get(reason, 0) + n
    return dict(sorted(out.items()))


def batch_metrics(con: duckdb.DuckDBPyConnection, contract: TableContract, fk_tables: dict[str, str | None],
                  previous_hwm: str | None) -> dict:
    total = con.execute(f"SELECT count(*) FROM {CHECKED_TABLE}").fetchone()[0]
    q_cols = _counts(con, "len(__errors) > 0", "__errors")
    w_cols = _counts(con, "len(__errors) = 0", "__warnings")
    metrics: dict = {
        "quarantine_by_reason": _by_reason(q_cols), "quarantine_by_column": q_cols,
        "warnings_by_reason": _by_reason(w_cols), "warnings_by_column": w_cols,
        "null_rate": {}, "orphan_rate": {}, "late_arrivals": None, "profiles": {},
    }
    if total:
        exprs = ", ".join(f"count(*) FILTER (WHERE {ident('__raw__' + c)} IS NULL)" for c in contract.column_names)
        nulls = con.execute(f"SELECT {exprs} FROM {CHECKED_TABLE}").fetchone()
        metrics["null_rate"] = {c: round(n / total, 4) for c, n in zip(contract.column_names, nulls)}
    for col in contract.foreign_keys:
        if fk_tables.get(col.name) is None:
            metrics["orphan_rate"][col.name] = {"parent": col.fk, "skipped": "parent table not loaded"}
            continue
        tag = f"{col.name}:orphan_fk"
        checked, orphans = con.execute(
            f"SELECT count({ident(col.name)}), count(*) FILTER (WHERE list_contains(__errors, '{tag}') "
            f"OR list_contains(__warnings, '{tag}')) FROM {CHECKED_TABLE}").fetchone()
        metrics["orphan_rate"][col.name] = {"parent": col.fk, "checked": checked, "orphans": orphans,
                                            "rate": round(orphans / checked, 4) if checked else 0.0}
    metrics["late_arrivals"] = _late_arrivals(con, contract, previous_hwm)
    for col in (c for c in contract.columns if c.profile):
        top = con.execute(f"SELECT CAST({ident(col.name)} AS VARCHAR) v, count(*) n FROM candidates "
                          f"GROUP BY 1 ORDER BY n DESC, v LIMIT {PROFILE_TOP_N}").fetchall()
        distinct = con.execute(f"SELECT count(DISTINCT {ident(col.name)}) FROM candidates").fetchone()[0]
        metrics["profiles"][col.name] = {"distinct": distinct, "top": [[v, n] for v, n in top]}
    return metrics


def _late_arrivals(con: duckdb.DuckDBPyConnection, contract: TableContract, previous_hwm: str | None) -> dict:
    part, event = contract.partition_column, contract.event_time_column
    out: dict = {"threshold_days": LATE_ARRIVAL_THRESHOLD_DAYS, "event_lag_rows": None, "late_partition_rows": None}
    if part and event:
        out["event_lag_rows"] = con.execute(
            f"SELECT count(*) FROM candidates WHERE date_diff('day', CAST({ident(event)} AS DATE), {ident(part)}) "
            f"> {LATE_ARRIVAL_THRESHOLD_DAYS}").fetchone()[0]
    if part:
        out["late_partition_rows"] = 0 if previous_hwm is None else con.execute(
            f"SELECT count(*) FROM candidates WHERE CAST({ident(part)} AS VARCHAR) <= ?", [previous_hwm]).fetchone()[0]
    return out


def unique_violations(con: duckdb.DuckDBPyConnection, contract: TableContract) -> dict:
    """Unique columns are reported, never quarantined: there is no principled way to pick the wrong row."""
    out = {}
    for col in (c for c in contract.columns if c.unique):
        values, rows = con.execute(
            f"SELECT count(*), coalesce(sum(n), 0) FROM (SELECT {ident(col.name)}, count(*) n "
            f"FROM silver.{ident(contract.table)} WHERE {ident(col.name)} IS NOT NULL GROUP BY 1 HAVING n > 1)"
        ).fetchone()
        out[col.name] = {"values": values, "rows": int(rows)}
    return out


def high_water_mark(con: duckdb.DuckDBPyConnection, contract: TableContract) -> str | None:
    column = contract.partition_column or "_source_file"
    return con.execute(f"SELECT CAST(max({ident(column)}) AS VARCHAR) FROM silver.{ident(contract.table)}").fetchone()[0]
