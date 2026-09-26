"""Comparisons for the update test: two delivery states file by file, and two warehouses row by row.

Row comparisons ignore what only says which run wrote a row (_run_id, _ingested_at, _gold_run_id, _source_run_ids),
so a warehouse updated incrementally can be checked against one built in a single load from the same final state.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import duckdb

from data_engineering.contracts.loader import TableContract
from data_engineering.pipelines.warehouse import ident

RUN_SPECIFIC = {"_run_id", "_ingested_at", "_gold_run_id", "_source_run_ids"}
# The quarantined raw record is the batch row as JSON: it also carries the batch row id and the run lineage, and a
# null for every column some other file of the same batch had. Only the delivered, non-null fields are compared.
_RAW_EXCLUDED = ("__rid", "_run_id", "_ingested_at")
_RAW_FIELDS = ("list_sort(list_transform(list_filter(json_keys(raw_record), k -> k NOT IN ("
               + ", ".join(f"'{k}'" for k in _RAW_EXCLUDED) + ") AND json_extract_string(raw_record, '$.\"' || k || "
               "'\"') IS NOT NULL), k -> k || '=' || json_extract_string(raw_record, '$.\"' || k || '\"')))")
_DAY = re.compile(r"year=(\d{4})/month=(\d{2})/day=(\d{2})/")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path, table: str) -> dict[str, Path]:
    found = {p.relative_to(root).as_posix(): p for p in (root / table).rglob("*") if p.is_file()} \
        if (root / table).is_dir() else {}
    found.update({p.name: p for p in root.glob(f"{table}.*") if p.is_file()})
    return found


def _days(rels: list[str]) -> list[str]:
    return sorted({"-".join(m.groups()) for m in map(_DAY.search, rels) if m})


def compare_states(before: Path, after: Path, tables: list[str]) -> dict[str, dict]:
    """Per table: files and bytes in each state, partition range, and how many shared paths hold identical bytes."""
    out = {}
    for table in tables:
        b, a = _files(before, table), _files(after, table)
        shared = sorted(set(b) & set(a))
        identical = sum(1 for rel in shared if b[rel].stat().st_size == a[rel].stat().st_size
                        and _sha256(b[rel]) == _sha256(a[rel]))
        days_b, days_a = _days(list(b)), _days(list(a))
        out[table] = {
            "files_before": len(b), "files_after": len(a), "shared_paths": len(shared),
            "identical_content": identical, "changed_content": len(shared) - identical,
            "only_after": len(set(a) - set(b)), "only_before": len(set(b) - set(a)),
            "bytes_before": sum(p.stat().st_size for p in b.values()),
            "bytes_after": sum(p.stat().st_size for p in a.values()),
            "days_before": [days_b[0], days_b[-1]] if days_b else None,
            "days_after": [days_a[0], days_a[-1]] if days_a else None,
        }
    return out


def _columns(con: duckdb.DuckDBPyConnection, catalog: str, schema: str, table: str) -> list[str]:
    rows = con.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog = ? "
                       "AND table_schema = ? AND table_name = ?", [catalog, schema, table]).fetchall()
    return sorted(r[0] for r in rows if r[0] not in RUN_SPECIFIC)


def _fingerprint(con: duckdb.DuckDBPyConnection, catalog: str, schema: str, table: str,
                 columns: list[str]) -> tuple[int, int]:
    """Row count and an order-independent sum of row hashes over `columns`."""
    row = ", ".join(ident(c) for c in columns)
    count, total = con.execute(f"SELECT count(*), coalesce(sum(CAST(hash(ROW({row})) AS HUGEINT)), 0) "
                               f"FROM {ident(catalog)}.{ident(schema)}.{ident(table)}").fetchone()
    return int(count), int(total)


def _has(con: duckdb.DuckDBPyConnection, catalog: str, schema: str, table: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_catalog = ? AND table_schema = ? "
                       "AND table_name = ?", [catalog, schema, table]).fetchone()[0] > 0


def compare_warehouses(left: Path, right: Path, silver_tables: list[str], gold_tables: list[str]) -> dict:
    """Is `left` equal to `right`, table by table, apart from run-specific lineage? Also compares the quarantine and
    the current file ledger entries."""
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{left.as_posix()}' AS l (READ_ONLY)")
        con.execute(f"ATTACH '{right.as_posix()}' AS r (READ_ONLY)")
        out: dict = {}
        targets = [("bronze", t) for t in silver_tables] + [("silver", t) for t in silver_tables]
        targets += [("gold", t) for t in gold_tables]
        for schema, table in targets:
            if not (_has(con, "l", schema, table) and _has(con, "r", schema, table)):
                out[f"{schema}.{table}"] = {"equal": False, "missing": True}
                continue
            cols = _columns(con, "l", schema, table)
            if cols != _columns(con, "r", schema, table):
                out[f"{schema}.{table}"] = {"equal": False, "columns_differ": True}
                continue
            lf, rf = _fingerprint(con, "l", schema, table, cols), _fingerprint(con, "r", schema, table, cols)
            out[f"{schema}.{table}"] = {"rows_left": lf[0], "rows_right": rf[0], "equal": lf == rf}
        quarantine = (f"SELECT table_name, count(*), coalesce(sum(CAST(hash(ROW(pk_value, reasons, details, "
                      f"{_RAW_FIELDS}, _source_file)) AS HUGEINT)), 0) FROM {{}}.quarantine.records "
                      "GROUP BY 1 ORDER BY 1")
        left_q, right_q = con.execute(quarantine.format("l")).fetchall(), con.execute(quarantine.format("r")).fetchall()
        out["quarantine.records"] = {"equal": left_q == right_q, "rows_left": sum(r[1] for r in left_q),
                                     "rows_right": sum(r[1] for r in right_q)}
        ledger = ("SELECT table_name, source_file, rows_read, file_size, file_modified_ms FROM {}.control.file_ledger "
                  "WHERE superseded_by_run IS NULL ORDER BY 1, 2")
        out["control.file_ledger(current)"] = {"equal": con.execute(ledger.format("l")).fetchall()
                                               == con.execute(ledger.format("r")).fetchall()}
        return out
    finally:
        con.close()


def key_changes(before: Path, after: Path, contracts: dict[str, TableContract], tables: list[str]) -> dict[str, dict]:
    """Per silver table: keys only before (withdrawn), only after (new), shared with the same content, and shared
    with different content, with the columns that changed and how often."""
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{before.as_posix()}' AS b (READ_ONLY)")
        con.execute(f"ATTACH '{after.as_posix()}' AS a (READ_ONLY)")
        out = {}
        for table in tables:
            c = contracts[table]
            if not (_has(con, "b", "silver", table) and _has(con, "a", "silver", table)):
                out[table] = None
                continue
            on = " AND ".join(f"x.{ident(k)} = y.{ident(k)}" for k in c.primary_key)
            t = ident(table)
            shared, same = con.execute(
                f"SELECT count(*), count(*) FILTER (WHERE x._content_hash = y._content_hash) "
                f"FROM b.silver.{t} x JOIN a.silver.{t} y ON {on}").fetchone()
            only_before = con.execute(f"SELECT count(*) FROM b.silver.{t} x ANTI JOIN a.silver.{t} y "
                                      f"ON {on}").fetchone()[0]
            only_after = con.execute(f"SELECT count(*) FROM a.silver.{t} y ANTI JOIN b.silver.{t} x "
                                     f"ON {on}").fetchone()[0]
            changed_cols = [col for col in c.column_names if col not in c.primary_key]
            counts = con.execute(
                "SELECT " + ", ".join(f"count(*) FILTER (WHERE x.{ident(col)} IS DISTINCT FROM y.{ident(col)})"
                                      for col in changed_cols) + f" FROM b.silver.{t} x JOIN a.silver.{t} y ON {on}"
            ).fetchone() if changed_cols else ()
            out[table] = {"keys_before": shared + only_before, "keys_after": shared + only_after, "shared": shared,
                          "shared_identical": same, "shared_changed": shared - same, "withdrawn": only_before,
                          "new": only_after,
                          "changed_columns": {col: n for col, n in zip(changed_cols, counts) if n}}
        return out
    finally:
        con.close()

