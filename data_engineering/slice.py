"""Copy a few customers out of a built warehouse into a small one, keeping every lineage column.

    uv run python -m data_engineering.slice --source data/warehouse_real.duckdb --target data/demo/slice.duckdb \
        --customers CUST1 CUST2

Used for the public demo (deploy/bundle.py): the demo serves real organizer rows for a handful of customers, and
the full warehouse (4.2 GB) never leaves the laptop. Rows are copied as they are, never rebuilt:

  * bronze, silver: customers, products and transactions of the chosen customers, with `_source_file`,
    `_ingested_at` and `_run_id` untouched;
  * gold: the serving tables the tools read (`customer_profile`, `customer_transactions`, `customer_products`) with
    `_source_table`, `_source_key`, `_source_run_ids` and `_gold_run_id`, and the `dispute_policy_inputs` view
    recreated from the source's own definition;
  * control: every silver and gold run (so the repository's freshness check sees the same history), and the file
    ledger rows of the source files the copied bronze rows came from;
  * control.demo_slice: one row per customer saying which scenario it was chosen for and from which warehouse.

The gold analytics tables (complaints, demand) are not copied: they aggregate every customer and would be wrong for
a slice. Tables are created from the source DDL, so column types are identical.
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Mapping
from pathlib import Path

import duckdb

CUSTOMER_TABLES = (("bronze", "customers"), ("bronze", "products"), ("bronze", "transactions"),
                   ("silver", "customers"), ("silver", "products"), ("silver", "transactions"),
                   ("gold", "customer_profile"), ("gold", "customer_transactions"), ("gold", "customer_products"))
ORDER = {"customers": "customer_id", "products": "product_id", "transactions": "transaction_id",
         "customer_profile": "customer_id", "customer_transactions": "transaction_id", "customer_products": "product_id"}
VIEWS = (("gold", "dispute_policy_inputs"),)
CONTROL = (("control", "runs"), ("control", "gold_runs"), ("control", "file_ledger"))


def _ddl(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> str:
    row = con.execute("SELECT sql FROM duckdb_tables() WHERE database_name = 'src' AND schema_name = ? "
                      "AND table_name = ?", [schema, name]).fetchone()
    if row is None:
        raise ValueError(f"source warehouse has no {schema}.{name}")
    return row[0]


def slice_warehouse(source: Path, target: Path, customers: Mapping[str, str], source_label: str) -> dict[str, int]:
    """Write `target` with only `customers` (customer_id -> scenario). Returns rows copied per table."""
    if not customers:
        raise ValueError("no customers to copy")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if not source.is_file():
        raise FileNotFoundError(f"source warehouse not found: {source}")
    if source.resolve() == target.resolve():
        raise ValueError("source and target are the same file")
    ids = sorted(customers)
    counts: dict[str, int] = {}
    con = duckdb.connect(str(target))
    ok = False
    try:
        quoted = source.as_posix().replace("'", "''")  # ATTACH takes no parameters
        con.execute(f"ATTACH '{quoted}' AS src (READ_ONLY)")
        con.execute("CREATE TEMP TABLE picked AS SELECT unnest(?::VARCHAR[]) AS customer_id", [ids])
        missing = [r[0] for r in con.execute("SELECT customer_id FROM picked WHERE customer_id NOT IN "
                                             "(SELECT customer_id FROM src.silver.customers) ORDER BY 1").fetchall()]
        if missing:
            raise ValueError(f"customers not in the source: {', '.join(missing)}")
        for schema in ("bronze", "silver", "gold", "control"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for schema, name in CUSTOMER_TABLES:
            con.execute(_ddl(con, schema, name))
            con.execute(f"INSERT INTO {schema}.{name} SELECT * FROM src.{schema}.{name} "
                        f"WHERE customer_id IN (SELECT customer_id FROM picked) ORDER BY {ORDER[name]}, "
                        f"{'_ingested_at' if schema != 'gold' else '_gold_run_id'}")
            counts[f"{schema}.{name}"] = con.execute(f"SELECT count(*) FROM {schema}.{name}").fetchone()[0]
        for schema, name in VIEWS:
            sql = con.execute("SELECT sql FROM duckdb_views() WHERE database_name = 'src' AND schema_name = ? "
                              "AND view_name = ?", [schema, name]).fetchone()[0]
            con.execute(sql)
        for schema, name in CONTROL:
            con.execute(_ddl(con, schema, name))
        con.execute("INSERT INTO control.runs SELECT * FROM src.control.runs ORDER BY started_at, run_id")
        con.execute("INSERT INTO control.gold_runs SELECT * FROM src.control.gold_runs ORDER BY started_at, run_id")
        con.execute("""INSERT INTO control.file_ledger SELECT * FROM src.control.file_ledger
                       WHERE source_file IN (SELECT DISTINCT _source_file FROM bronze.customers UNION
                                             SELECT DISTINCT _source_file FROM bronze.products UNION
                                             SELECT DISTINCT _source_file FROM bronze.transactions)
                       ORDER BY table_name, source_file""")
        counts["control.file_ledger"] = con.execute("SELECT count(*) FROM control.file_ledger").fetchone()[0]
        con.execute("CREATE TABLE control.demo_slice(customer_id VARCHAR, scenario VARCHAR, source_warehouse "
                    "VARCHAR, latest_silver_run VARCHAR, latest_gold_run VARCHAR)")
        runs = con.execute("SELECT (SELECT run_id FROM control.runs ORDER BY started_at DESC LIMIT 1), "
                           "(SELECT run_id FROM control.gold_runs ORDER BY started_at DESC LIMIT 1)").fetchone()
        con.executemany("INSERT INTO control.demo_slice VALUES (?, ?, ?, ?, ?)",
                        [[cid, customers[cid], source_label, runs[0], runs[1]] for cid in ids])
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
        ok = True
    finally:
        con.close()
        if not ok:  # never leave a half-written slice behind
            target.unlink(missing_ok=True)
    return counts


def content_digest(warehouse: Path) -> str:
    """SHA-256 over every row of every sliced table, in a fixed order. Equal digests mean equal content even when
    the DuckDB file bytes differ."""
    h = hashlib.sha256()
    with duckdb.connect(str(warehouse), read_only=True) as con:
        tables = [*CUSTOMER_TABLES, *CONTROL, ("control", "demo_slice")]
        for schema, name in tables:
            h.update(f"{schema}.{name}\n".encode())
            for row in con.execute(f"SELECT * FROM {schema}.{name} ORDER BY ALL").fetchall():
                h.update(repr(row).encode())
                h.update(b"\n")
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--customers", nargs="+", required=True)
    args = parser.parse_args(argv)
    counts = slice_warehouse(args.source, args.target, {c: "manual" for c in args.customers}, args.source.name)
    print({**counts, "bytes": args.target.stat().st_size})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
