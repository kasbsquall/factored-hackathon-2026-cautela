"""Quality checks for one gold table, run inside the build transaction before it commits.

Gold is derived data, so any violation is a bug in the build or a silver guarantee that no longer holds. Every
check is therefore blocking: the table rolls back and the run fails with the list of violations.

Checks: primary key unique and not null, NOT NULL columns, value lists, numeric ranges, lineage present on every
row, the SQL result matching the contract (names and types), and the contract's reconciliation queries.
"""

from __future__ import annotations

import duckdb

from data_engineering.gold.contract import LINEAGE_COLUMNS, GoldContract
from data_engineering.pipelines.warehouse import ident, literal


class GoldQualityError(RuntimeError):
    def __init__(self, table: str, failures: list[dict]) -> None:
        self.table = table
        self.failures = failures
        summary = "; ".join(f"{f['check']}({f.get('column') or '-'})={f['violations']}" for f in failures)
        super().__init__(f"gold.{table} failed quality checks: {summary}")


def expected_schema(contract: GoldContract) -> dict[str, str]:
    """Column name to type the SQL must return. Materialized SQL returns two lineage columns; the build adds the
    other two. A view returns all four itself."""
    cols = {c.name: c.type for c in contract.columns}
    lineage = LINEAGE_COLUMNS if not contract.materialized else {
        k: v for k, v in LINEAGE_COLUMNS.items() if k in ("_source_key", "_source_run_ids")}
    return {**cols, **lineage}


def schema_mismatches(con: duckdb.DuckDBPyConnection, contract: GoldContract) -> list[str]:
    rows = con.execute(f"DESCRIBE SELECT * FROM ({contract.sql}) AS q").fetchall()
    actual = {r[0]: r[1] for r in rows}
    expected = expected_schema(contract)
    problems = [f"missing column {c}" for c in expected if c not in actual]
    problems += [f"unexpected column {c}" for c in actual if c not in expected]
    problems += [f"{c}: contract says {t}, SQL returns {actual[c]}" for c, t in expected.items()
                 if c in actual and actual[c] != t]
    return problems


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> float:
    value = con.execute(sql).fetchone()[0]
    return float(value or 0)


def run_checks(con: duckdb.DuckDBPyConnection, contract: GoldContract) -> list[dict]:
    """Return one entry per check with its violation count (0 means passed)."""
    t = f"gold.{ident(contract.table)}"
    results: list[dict] = []

    def add(check: str, violations: float, column: str | None = None) -> None:
        results.append({"check": check, "column": column, "violations": violations})

    pk = ", ".join(ident(k) for k in contract.primary_key)
    add("primary_key_unique", _count(con, f"SELECT count(*) FROM (SELECT {pk} FROM {t} GROUP BY ALL HAVING count(*) > 1)"))
    for col in contract.columns:
        c = ident(col.name)
        if not col.nullable:
            add("not_null", _count(con, f"SELECT count(*) FROM {t} WHERE {c} IS NULL"), col.name)
        if col.allowed_values:
            values = ", ".join(literal(v) for v in col.allowed_values)
            add("allowed_values", _count(con, f"SELECT count(*) FROM {t} WHERE {c} NOT IN ({values})"), col.name)
        if col.min is not None:
            add("min", _count(con, f"SELECT count(*) FROM {t} WHERE {c} < {col.min}"), col.name)
        if col.max is not None:
            add("max", _count(con, f"SELECT count(*) FROM {t} WHERE {c} > {col.max}"), col.name)
    add("lineage_present", _count(
        con, f"SELECT count(*) FROM {t} WHERE _source_table IS NULL OR _source_key IS NULL OR _gold_run_id IS NULL "
             "OR _source_run_ids IS NULL OR len(_source_run_ids) = 0"))
    for rec in contract.reconciliation:
        add(f"reconciliation:{rec.name}", _count(con, rec.sql))
    return results


def enforce(contract: GoldContract, results: list[dict]) -> None:
    failures = [r for r in results if r["violations"]]
    if failures:
        raise GoldQualityError(contract.table, failures)
