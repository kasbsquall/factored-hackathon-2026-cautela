"""Read customers and their transactions from the organizer files with DuckDB.

The builder reads the raw CSV partitions directly (``data/raw``), applying the
same primary-key rule as silver (one row per ``transaction_id``, latest
``process_date``) and dropping rows whose amount or date cannot be cast. The
counts of dropped rows are returned so the manifest can report them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import duckdb

COUNTRY_CODES = {"México": "MX", "Mexico": "MX", "Colombia": "CO", "Argentina": "AR"}
TX_COLUMNS = ("transaction_id", "transaction_date", "process_date", "customer_id", "transaction_type", "amount",
              "currency", "channel", "merchant_name", "merchant_category", "transaction_city",
              "transaction_country", "transaction_status")


def source_fingerprint(raw_dir: Path) -> str:
    """Hash of relative path and size of every input file (cheap, content-free)."""
    files = sorted([raw_dir / "customers.csv"] + list((raw_dir / "transactions").rglob("*.csv")))
    h = hashlib.sha256()
    for f in files:
        h.update(f"{f.relative_to(raw_dir).as_posix()}:{f.stat().st_size}\n".encode())
    return h.hexdigest()[:16]


def load_customers(raw_dir: Path) -> list[dict]:
    con = duckdb.connect()
    rows = con.execute(
        "SELECT customer_id, country, segment FROM read_csv(?, header=true, all_varchar=true)",
        [str(raw_dir / "customers.csv")],
    ).fetchall()
    out = []
    for cid, country, segment in rows:
        code = COUNTRY_CODES.get(country or "")
        if cid and code and segment:
            out.append({"customer_id": cid, "country": code, "segment": segment})
    return out


def load_transactions(raw_dir: Path, customer_ids: list[str]) -> tuple[dict[str, list[dict]], dict]:
    """Transactions of the given customers, grouped by customer and sorted by time."""
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE sel AS SELECT unnest(?::VARCHAR[]) AS customer_id", [customer_ids])
    glob = str(raw_dir / "transactions" / "**" / "*.csv").replace("\\", "/")
    cols = ", ".join(f"t.{c}" for c in TX_COLUMNS)
    con.execute(f"""
        CREATE TEMP TABLE raw AS
        SELECT {cols} FROM read_csv('{glob}', header=true, union_by_name=true, all_varchar=true) t
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
