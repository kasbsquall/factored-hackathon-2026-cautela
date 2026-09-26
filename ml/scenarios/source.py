"""Read customers and their transactions from the organizer files with DuckDB.

The source is either a local copy (``data/raw``) or the organizer bucket
(``s3://<bucket>/data``, credentials from the git-ignored ``.env`` through
data_engineering/pipelines/source.py). Both hold the same CSV partitions, so
either rebuilds the same case files. The reader applies the silver primary-key
rule (one row per ``transaction_id``, latest ``process_date``) and drops rows
whose amount or date cannot be cast; the counts go to the manifest.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime

import duckdb

from data_engineering.pipelines.env import load_env_file
from data_engineering.pipelines.source import SourceLocation, connect_source, parse_source

COUNTRY_CODES = {"México": "MX", "Mexico": "MX", "Colombia": "CO", "Argentina": "AR"}
TX_COLUMNS = ("transaction_id", "transaction_date", "process_date", "customer_id", "transaction_type", "amount",
              "currency", "channel", "merchant_name", "merchant_category", "transaction_city",
              "transaction_country", "transaction_status")
S3_DATA_PREFIX = "data"  # organizer bucket layout: s3://<bucket>/data/<table>/...


def default_source() -> str:
    """data/raw when a local copy exists, else the bucket named by LATAM_BANK_S3_URI plus /data."""
    if os.path.isdir("data/raw/transactions"):
        return "data/raw"
    load_env_file(".env")
    bucket = os.environ.get("LATAM_BANK_S3_URI")
    if not bucket:
        raise SystemExit("no data/raw copy and no LATAM_BANK_S3_URI in .env: pass --source")
    return bucket.rstrip("/") + "/" + S3_DATA_PREFIX


def open_source(uri: str) -> tuple[duckdb.DuckDBPyConnection, SourceLocation]:
    load_env_file(".env")
    loc = parse_source(uri)
    con = duckdb.connect()
    connect_source(con, loc)
    return con, loc


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def source_fingerprint(con: duckdb.DuckDBPyConnection, loc: SourceLocation) -> str:
    """Hash of the relative paths of every input file; identical for the local copy and the bucket."""
    files = [f"{loc.root}/customers.csv"] + [r[0] for r in con.execute(
        "SELECT file FROM glob(?)", [f"{loc.root}/transactions/**/*.csv"]).fetchall()]
    h = hashlib.sha256()
    for rel in sorted(loc.relative(f) for f in files):
        h.update(f"{rel}\n".encode())
    return h.hexdigest()[:16]


def load_customers(con: duckdb.DuckDBPyConnection, loc: SourceLocation) -> list[dict]:
    rows = con.execute(
        f"SELECT customer_id, country, segment FROM read_csv({_q(loc.root + '/customers.csv')}, header=true, "
        "all_varchar=true)").fetchall()
    out = []
    for cid, country, segment in rows:
        code = COUNTRY_CODES.get(country or "")
        if cid and code and segment:
            out.append({"customer_id": cid, "country": code, "segment": segment})
    return out


def load_transactions(con: duckdb.DuckDBPyConnection, loc: SourceLocation,
                      customer_ids: list[str]) -> tuple[dict[str, list[dict]], dict]:
    """Transactions of the given customers, grouped by customer and sorted by time."""
    con.execute("CREATE OR REPLACE TEMP TABLE sel AS SELECT unnest(?::VARCHAR[]) AS customer_id", [customer_ids])
    glob = _q(f"{loc.root}/transactions/**/*.csv")
    cols = ", ".join(f"t.{c}" for c in TX_COLUMNS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE raw AS
        SELECT {cols} FROM read_csv({glob}, header=true, union_by_name=true, all_varchar=true) t
        JOIN sel USING (customer_id)
    """)
    total = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    rows = con.execute("""
        SELECT transaction_id, TRY_CAST(transaction_date AS TIMESTAMP) AS ts, customer_id, transaction_type,
               TRY_CAST(amount AS DOUBLE) AS amount, currency, channel, merchant_name, merchant_category,
               transaction_city, transaction_country, transaction_status
        FROM raw
        WHERE TRY_CAST(transaction_date AS TIMESTAMP) IS NOT NULL AND TRY_CAST(amount AS DOUBLE) IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY TRY_CAST(process_date AS DATE) DESC) = 1
    """).fetchall()
    by_customer: dict[str, list[dict]] = {}
    for r in rows:
        ts: datetime = r[1]
        tx = {"transaction_id": r[0], "ts": ts.isoformat(sep=" "), "date": ts.date(), "customer_id": r[2],
              "transaction_type": r[3], "amount": round(r[4], 2), "currency": r[5], "channel": r[6],
              "merchant_name": r[7], "merchant_category": r[8], "transaction_city": r[9],
              "transaction_country": r[10], "transaction_status": r[11]}
        by_customer.setdefault(r[2], []).append(tx)
    for txs in by_customer.values():
        txs.sort(key=lambda t: (t["ts"], t["transaction_id"]))
    stats = {"raw_rows_for_selected_customers": total, "rows_kept": len(rows), "rows_dropped_uncastable_or_duplicate": total - len(rows)}
    return by_customer, stats
