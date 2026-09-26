"""Cut the evaluation warehouse from the organizer warehouse: only the customers of the held-out test split.

    uv run python -m eval.slice [--source data/warehouse_real.duckdb] [--out data/eval/warehouse_eval.duckdb]

The agent reads gold serving tables (and silver for identity and products) through WarehouseRepository. The slice
keeps exactly those tables, with every row of the selected customers and nothing else, plus the control tables the
repository's freshness check reads. Customers are found through the pseudonymous `customer_ref` of the case files
(sha256 of the customer id, ml/scenarios/build.py). The output lives under data/ (git-ignored): it is organizer
data. `content_hash` is a sha256 over the ordered rows, so two builds from the same source agree even though DuckDB
files are not byte-identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

from eval.paths import DISPUTES_DIR, REAL_WAREHOUSE, SLICE_PATH

TABLES = {  # table -> ordering key for the content hash
    "silver.customers": "customer_id",
    "silver.products": "product_id",
    "gold.customer_profile": "customer_id",
    "gold.customer_transactions": "transaction_id",
}
CONTROL = ("control.runs", "control.gold_runs")


def customer_ref(customer_id: str) -> str:
    """Same pseudonym as ml/scenarios/build.py; kept as a copy so the slice does not import the case builder."""
    return "cust_" + hashlib.sha256(customer_id.encode()).hexdigest()[:12]


def test_customer_refs(cases_dir: Path = DISPUTES_DIR) -> set[str]:
    path = cases_dir / "test.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found: rebuild it with `uv run python -m ml.scenarios.build --verify`")
    with path.open(encoding="utf-8") as fh:
        return {json.loads(line)["customer_ref"] for line in fh if line.strip()}


def content_hash(con: duckdb.DuckDBPyConnection) -> str:
    h = hashlib.sha256()
    for table, key in sorted(TABLES.items()):
        cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
        for row in con.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall():
            h.update(json.dumps(dict(zip(cols, row, strict=True)), default=str, sort_keys=True).encode())
            h.update(b"\n")
    return h.hexdigest()


def build_slice(source: Path = REAL_WAREHOUSE, out: Path = SLICE_PATH, refs: set[str] | None = None) -> dict:
    refs = refs if refs is not None else test_customer_refs()
    if not source.exists():
        raise SystemExit(f"{source} not found: build the organizer warehouse first (make pipeline-s3 gold)")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    with duckdb.connect(str(source), read_only=True) as src:
        ids = [cid for (cid,) in src.execute("SELECT customer_id FROM silver.customers").fetchall()
               if customer_ref(cid) in refs]
        view_sql = src.execute("SELECT sql FROM duckdb_views() WHERE schema_name = 'gold' "
                               "AND view_name = 'dispute_policy_inputs'").fetchone()[0]
    missing = len(refs) - len(ids)
    con = duckdb.connect(str(tmp))
    try:
        con.execute(f"ATTACH '{source.as_posix()}' AS src (READ_ONLY)")
        con.execute("CREATE TEMP TABLE sel AS SELECT unnest(?::VARCHAR[]) AS customer_id", [sorted(ids)])
        for schema in ("silver", "gold", "control"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for table in TABLES:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table} WHERE customer_id IN "
                        "(SELECT customer_id FROM sel)")
        for table in CONTROL:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
        con.execute(view_sql)
        con.execute("DETACH src")
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
        digest = content_hash(con)
    finally:
        con.close()
    out.unlink(missing_ok=True)
    tmp.replace(out)
    return {"customers": len(ids), "refs_not_found": missing, "rows": counts, "content_hash": digest,
            "source": source.name}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=REAL_WAREHOUSE)
    ap.add_argument("--out", type=Path, default=SLICE_PATH)
    args = ap.parse_args()
    print(json.dumps(build_slice(args.source, args.out), indent=2))


if __name__ == "__main__":
    main()
