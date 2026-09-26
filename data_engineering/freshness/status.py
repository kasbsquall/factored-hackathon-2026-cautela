"""Freshness of a built warehouse, measured against the freshness policy.

    uv run python -m data_engineering.freshness.status --target data/warehouse.duckdb
    uv run python -m data_engineering.freshness.status --target data/warehouse.duckdb --as-of 2026-06-18

Per silver table: the newest partition and event date, the newest loaded file version, and how many days that is
behind the reference date. A daily table is measured by its newest partition, a snapshot table by the modification
time of its newest loaded file (snapshots carry no snapshot date column). The reference is --as-of (default today,
UTC); the report also gives each daily table's lag behind the newest partition of any daily table in the warehouse
(the dataset clock), which is what matters when the data is a static delivery.

Gold is stale when its latest successful run is older than the latest successful silver run (the tool repository
refuses to start in that case) or when a materialized gold table was built from silver that has changed since.
Exit code 0 when everything is within policy, 1 when something is stale or not loaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from data_engineering.contracts.loader import TableContract, load_contracts
from data_engineering.gold.build import source_marks
from data_engineering.gold.contract import load_gold_contracts
from data_engineering.pipelines.warehouse import ident, table_exists

# Maximum days a table may be behind the reference date. Daily partitions for day D are expected by D+1 (the
# organizer data has event times at most one day after the partition date). Monthly snapshots are expected every
# month, with a few days of slack. The dictionary gives no cadence for full snapshots, so none is enforced.
MAX_LAG_DAYS: dict[str, int | None] = {"daily": 1, "monthly_snapshot": 35, "full_snapshot": None}


def _newest_file(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, date | None, datetime | None]:
    files, modified_ms, loaded_at = con.execute(
        "SELECT count(*), max(file_modified_ms), max(loaded_at) FROM control.file_ledger "
        "WHERE table_name = ? AND superseded_by_run IS NULL", [table]).fetchone()
    modified = datetime.fromtimestamp(modified_ms / 1000, timezone.utc).date() if modified_ms is not None else None
    return files, modified, loaded_at


def table_freshness(con: duckdb.DuckDBPyConnection, contract: TableContract, as_of: date) -> dict:
    name = contract.table
    threshold = MAX_LAG_DAYS[contract.partitioning]
    out: dict = {"partitioning": contract.partitioning, "max_lag_days": threshold}
    if not table_exists(con, "silver", name):
        return {**out, "status": "not_loaded"}
    part, event = contract.partition_column, contract.event_time_column
    exprs = ["count(*)",
             f"CAST(max({ident(part)}) AS DATE)" if part else "NULL",
             f"CAST(max({ident(event)}) AS DATE)" if event else "NULL"]
    rows, max_partition, max_event = con.execute(f"SELECT {', '.join(exprs)} FROM silver.{ident(name)}").fetchone()
    files, newest_file, loaded_at = _newest_file(con, name)
    measured = max_partition if contract.partitioning == "daily" else newest_file
    lag = (as_of - measured).days if measured else None
    if lag is None:
        status = "no_date"
    elif threshold is None:
        status = "no_policy"
    else:
        status = "fresh" if lag <= threshold else "stale"
    return {**out, "rows": rows, "max_partition": max_partition, "max_event_date": max_event, "files": files,
            "newest_file_modified": newest_file, "last_loaded_at": loaded_at, "lag_days": lag, "status": status}


def gold_freshness(con: duckdb.DuckDBPyConnection) -> dict:
    if not table_exists(con, "control", "gold_runs"):
        return {"status": "not_built", "tables": {}}
    silver_run, gold_run = con.execute(
        "SELECT (SELECT max(started_at) FROM control.runs WHERE status = 'succeeded'), "
        "(SELECT max(started_at) FROM control.gold_runs WHERE status = 'succeeded')").fetchone()
    older = gold_run is None or (silver_run is not None and gold_run < silver_run)
    tables = {}
    for name, contract in sorted(load_gold_contracts().items()):
        if not contract.materialized:
            continue
        row = con.execute("SELECT source_marks FROM control.gold_state WHERE gold_table = ?", [name]).fetchone()
        try:
            current = row is not None and json.loads(row[0]) == source_marks(con, contract)
        except ValueError:  # a silver source is missing
            current = False
        tables[name] = "current" if current else "stale"
    stale = older or any(v != "current" for v in tables.values())
    return {"latest_silver_run": silver_run, "latest_gold_run": gold_run, "gold_older_than_silver": older,
            "tables": tables, "status": "stale" if stale else "fresh"}


def snapshot(con: duckdb.DuckDBPyConnection, as_of: date, tables: list[str] | None = None) -> dict:
    contracts = load_contracts()
    selected = tables or sorted(contracts)
    per_table = {name: table_freshness(con, contracts[name], as_of) for name in selected}
    daily = [t["max_partition"] for t in per_table.values()
             if t["partitioning"] == "daily" and t.get("max_partition")]
    clock = max(daily) if daily else None
    for t in per_table.values():
        if t["partitioning"] == "daily" and t.get("max_partition") and clock:
            t["lag_vs_dataset_clock_days"] = (clock - t["max_partition"]).days
    return {"as_of": as_of, "dataset_clock": clock, "tables": per_table, "gold": gold_freshness(con)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freshness of a Cautela warehouse against the policy")
    parser.add_argument("--target", default="data/warehouse.duckdb")
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(timezone.utc).date(),
                        help="reference date (default today, UTC)")
    parser.add_argument("--tables", nargs="*", help="subset of silver tables")
    args = parser.parse_args(argv)
    if not Path(args.target).exists():
        print(f"{args.target} does not exist", file=sys.stderr)
        return 1
    with duckdb.connect(args.target, read_only=True) as con:
        snap = snapshot(con, args.as_of, args.tables)
    print(f"as of {snap['as_of']}, dataset clock {snap['dataset_clock']}")
    print(f"{'table':<26}{'newest':>12}{'lag':>6}{'limit':>7}{'vs clock':>10}  status")
    for name, t in snap["tables"].items():
        newest = t.get("max_partition") or t.get("newest_file_modified") or "-"
        lag = "-" if t.get("lag_days") is None else t["lag_days"]
        limit = "-" if t["max_lag_days"] is None else t["max_lag_days"]
        print(f"{name:<26}{str(newest):>12}{lag:>6}{limit:>7}{t.get('lag_vs_dataset_clock_days', '-'):>10}  "
              f"{t['status']}")
    gold = snap["gold"]
    print(f"gold: {gold['status']}" + (" (older than the latest silver run)" if gold.get("gold_older_than_silver")
                                        else ""))
    within = all(t["status"] in ("fresh", "no_policy") for t in snap["tables"].values()) and gold["status"] == "fresh"
    return 0 if within else 1


if __name__ == "__main__":
    sys.exit(main())
