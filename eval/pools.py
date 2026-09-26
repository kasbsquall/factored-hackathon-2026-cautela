"""Candidate pool sizes: in the suite (scenario mix) and among real organizer customers (real-data distribution).

    uv run python -m eval.pools        # adds results["real_pool_distribution"] to eval/results.json

The agent ranks the logged-in customer's own transactions of the last 90 days (agent/orchestrator/core.py,
POOL_ARGS). The dispute scenarios were built only where that pool had at least 3 transactions (ml/scenarios/build.py,
min_pool=3), so the suite has no pools of 1 or 2. This module measures how common each pool size is in the real
organizer warehouse, read-only, so the report can say how representative the scenario mix is.
"""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache

import duckdb

from eval.paths import DISPUTES_DIR, REAL_WAREHOUSE, RESULTS_PATH

BUCKETS = ("0", "1", "2-3", "4+")
# Report dates of the original test split fall in 2026-01..2026-06; one snapshot at the start of each month.
SNAPSHOTS = ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01")
WINDOWS = (90, 120)


def bucket(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2-3" if n <= 3 else "4+"


@lru_cache(maxsize=1)
def suite_pool_sizes() -> dict[str, int]:
    """Source case id (the suite's `group`) -> number of candidate transactions in its 90-day pool."""
    out = {}
    for line in (DISPUTES_DIR / "test.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            out[case["case_id"]] = len(case["candidates"])
    return out


@lru_cache(maxsize=1)
def fresh_pool_sizes() -> dict[str, int]:
    """Same as suite_pool_sizes for the test_fresh cases that eval_fresh (eval/fresh) is built from."""
    path = DISPUTES_DIR / "test_fresh.jsonl"
    if not path.exists():
        return {}
    return {c["case_id"]: len(c["candidates"]) for c in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def pool_bucket_of(group: str) -> str | None:
    n = suite_pool_sizes().get(group)
    if n is None and group.startswith("test_fresh-"):
        n = fresh_pool_sizes().get(group)
    return None if n is None else bucket(n)


def real_distribution(path=REAL_WAREHOUSE) -> dict:
    """Share of Active customer x month snapshots in each pool bucket, for every transaction in the window (what
    the agent ranks) and for approved or pending debits only (what a customer could plausibly dispute)."""
    sql = """
        WITH snap AS (SELECT CAST(unnest(?) AS TIMESTAMP) AS ref),
        cust AS (SELECT customer_id FROM silver.customers WHERE customer_status = 'Active')
        SELECT s.ref, c.customer_id,
               count(t.transaction_id) AS all_tx,
               count(t.transaction_id) FILTER (WHERE t.transaction_status IN ('Approved', 'Pending')
                                               AND t.transaction_type <> 'Deposit') AS debits
        FROM snap s CROSS JOIN cust c
        LEFT JOIN gold.customer_transactions t
          ON t.customer_id = c.customer_id AND t.transaction_date <= s.ref
         AND t.transaction_date > s.ref - to_days(CAST(? AS INTEGER))
        GROUP BY ALL
    """
    out: dict = {"source": "data/warehouse_real.duckdb (organizer data, read-only)",
                 "population": "Active customers x monthly snapshots " + ", ".join(SNAPSHOTS), "windows": {}}
    with duckdb.connect(str(path), read_only=True) as con:
        for days in WINDOWS:
            rows = con.execute(sql, [list(SNAPSHOTS), days]).fetchall()
            entry = {}
            for label, idx in (("all_transactions", 2), ("approved_or_pending_debits", 3)):
                sizes = sorted(r[idx] for r in rows)
                counts = Counter(bucket(n) for n in sizes)
                entry[label] = {"snapshots": len(sizes), "median": sizes[len(sizes) // 2],
                                "p90": sizes[int(0.9 * (len(sizes) - 1))],
                                "share": {b: round(counts.get(b, 0) / len(sizes), 4) for b in BUCKETS},
                                "count": {b: counts.get(b, 0) for b in BUCKETS}}
            out["windows"][f"{days}d"] = entry
    return out


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    results["real_pool_distribution"] = real_distribution()
    sizes = Counter(bucket(n) for n in suite_pool_sizes().values())
    results["suite_pool_distribution"] = {"cases": sum(sizes.values()),
                                          "count": {b: sizes.get(b, 0) for b in BUCKETS}}
    RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"real": results["real_pool_distribution"]["windows"],
                      "suite": results["suite_pool_distribution"]}, indent=1))


if __name__ == "__main__":
    main()
