"""Update test on organizer data: an earlier delivery state first, then the current one, into the same warehouse.

    uv run python -m data_engineering.freshness.demo            # make freshness-demo

The organizer bucket holds the current delivery under data/ and an earlier state of it under data_backup_20260831/.
Both are copied (only files that changed since the last copy) into data/freshness/cache/, which is git-ignored.
A local landing directory then plays the delivery location: it first holds the earlier state, the pipeline and gold
run, and then it is overwritten with the current state, the way a source is updated in place, and both run again.
The result is checked against a second warehouse built in one load from the current state, and a rerun must change
nothing. Numbers go to data/freshness/results.json and to the committed report, which holds aggregates only.

--before-source and --after-source take any local path or s3:// URI instead of the bucket prefixes (the tests use
two local directories).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from data_engineering.contracts.loader import dependency_order, load_contracts
from data_engineering.freshness import compare, stage, status
from data_engineering.freshness.report import render
from data_engineering.gold.contract import load_gold_contracts
from data_engineering.gold.run import run_gold
from data_engineering.pipelines.env import load_env_file
from data_engineering.pipelines.report import _default
from data_engineering.pipelines.run import run_pipeline
from data_engineering.pipelines.source import SOURCE_ENV_VAR, connect_source, parse_source

# digital_events and campaign_sends are 3.8 GB and 0.3 GB of CSV per state; no gold table reads them. They are left
# out by default and compared file by file only (see list_only_tables); --tables can add them.
LARGE_TABLES = ("digital_events", "campaign_sends")
DEFAULT_REPORT = Path("data_engineering/reports/freshness_backup_vs_current.md")


def _bucket_uri(prefix: str, env: dict) -> str:
    uri = env.get(SOURCE_ENV_VAR)
    if not uri:
        raise ValueError(f"set {SOURCE_ENV_VAR} in .env, or pass --before-source and --after-source")
    loc = parse_source(uri)
    if loc.kind != "s3":
        raise ValueError(f"{SOURCE_ENV_VAR} is not an s3:// URI")
    return f"s3://{loc.bucket}/{prefix.strip('/')}"


class Timer:
    def __init__(self) -> None:
        self.steps: dict[str, float] = {}

    def run(self, name: str, fn, *args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self.steps[name] = round(time.perf_counter() - start, 1)


def _copy(source: str, tables: list[str], dest: Path, workers: int) -> dict:
    con = duckdb.connect()
    try:
        connect_source(con, parse_source(source))
        return stage.copy_state(con, source, tables, dest, workers)
    finally:
        con.close()


def _run_summary(report: dict) -> dict:
    """The parts of a quality report the update test reads."""
    out = {"run_id": report["run_id"], "totals": report["totals"], "tables": {}}
    for name, sec in report["tables"].items():
        r = sec["rows"]
        out["tables"][name] = {
            "files": {k: sec["files"].get(k) for k in ("discovered", "new", "rewritten", "already_loaded",
                                                          "missing_from_source")},
            "rows": {k: r.get(k) for k in ("bronze_in", "quarantined", "valid", "exact_duplicates", "inserted",
                                           "updated", "unchanged", "silver_total", "replayed")},
            "retracted": r.get("retracted"), "drift_events": sec["drift_events"],
            "quarantine_by_reason": sec["quarantine_by_reason"], "high_water_mark": sec["high_water_mark"],
            "late_partition_rows": (sec.get("late_arrivals") or {}).get("late_partition_rows"),
        }
    return out


def _gold_summary(report: dict) -> dict:
    return {name: {"mode": sec["mode"], "rows_total": sec["rows_total"], "keys_changed": sec["keys_changed"],
                   "checks_failed": sum(1 for c in sec["checks"] if c["violations"]), "checks": len(sec["checks"])}
            for name, sec in report["tables"].items()}


def _snap(db: Path, tables: list[str], today: date) -> dict:
    with duckdb.connect(str(db), read_only=True) as con:
        return status.snapshot(con, today, tables)


def run_demo(before: str, after: str, work: Path, tables: list[str], baseline: bool = True,
             workers: int = stage.DEFAULT_WORKERS, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    contracts = load_contracts()
    tables = dependency_order(contracts, tables)
    gold_tables = sorted(t for t, c in load_gold_contracts().items() if c.materialized)
    listed = [t for t in LARGE_TABLES if t not in tables]
    cache_before, cache_after, landing = work / "cache" / "before", work / "cache" / "after", work / "landing"
    update_db, before_db, full_db = work / "update.duckdb", work / "before.duckdb", work / "full.duckdb"
    reports = work / "reports"
    for db in (update_db, before_db, full_db):
        db.unlink(missing_ok=True)
    timer = Timer()
    results: dict = {"before_source": before, "after_source": after, "tables": tables, "gold_tables": gold_tables,
                     "listed_only": listed, "today": today, "started_at": datetime.now(timezone.utc)}

    results["copy_before"] = timer.run("copy earlier state from source", _copy, before, tables, cache_before, workers)
    results["copy_after"] = timer.run("copy current state from source", _copy, after, tables, cache_after, workers)
    results["listings"] = timer.run("list large tables", lambda: {
        "before": _listing(before, listed), "after": _listing(after, listed)}) if listed else {}
    results["files"] = timer.run("compare states file by file", compare.compare_states, cache_before, cache_after,
                                 tables)

    timer.run("stage earlier state", _copy, str(cache_before), tables, landing, workers)
    first = timer.run("pipeline, earlier state", run_pipeline, str(landing), update_db, tables, reports_dir=reports)
    gold_first = timer.run("gold, earlier state", run_gold, update_db, reports_dir=reports)
    results["run_before"], results["gold_before"] = _run_summary(first), _gold_summary(gold_first)
    results["freshness_before"] = _snap(update_db, tables, today)
    shutil.copy2(update_db, before_db)

    results["stage_after"] = timer.run("stage current state", _copy, str(cache_after), tables, landing, workers)
    second = timer.run("pipeline, current state (incremental)", run_pipeline, str(landing), update_db, tables,
                       reports_dir=reports)
    results["freshness_after_silver"] = _snap(update_db, tables, today)
    gold_second = timer.run("gold, current state (incremental)", run_gold, update_db, reports_dir=reports)
    results["run_after"], results["gold_after"] = _run_summary(second), _gold_summary(gold_second)
    results["freshness_after"] = _snap(update_db, tables, today)
    results["key_changes"] = timer.run("compare keys before and after", compare.key_changes, before_db, update_db,
                                       contracts, tables)

    rerun = timer.run("pipeline rerun, nothing changed", run_pipeline, str(landing), update_db, tables,
                      reports_dir=reports)
    results["freshness_after_rerun_silver"] = _snap(update_db, tables, today)["gold"]
    gold_rerun = timer.run("gold rerun, nothing changed", run_gold, update_db, reports_dir=reports)
    results["rerun"], results["gold_rerun"] = _run_summary(rerun), _gold_summary(gold_rerun)

    if baseline:
        timer.run("pipeline, current state in one load", run_pipeline, str(landing), full_db, tables,
                  reports_dir=reports)
        timer.run("gold, current state in one load", run_gold, full_db, reports_dir=reports)
        results["equivalence"] = timer.run("compare with the one-load warehouse", compare.compare_warehouses,
                                           update_db, full_db, tables, gold_tables)
    results["timings_s"] = timer.steps
    results["finished_at"] = datetime.now(timezone.utc)
    return results


def _listing(source: str, tables: list[str]) -> dict:
    """File count, bytes and partition range per table, from the listing only (no file body is read)."""
    con = duckdb.connect()
    try:
        loc = parse_source(source)
        connect_source(con, loc)
        files = stage.list_state(con, loc, tables)
    finally:
        con.close()
    out = {}
    for t in tables:
        mine = [f for f in files if f.rel_path.startswith(t + "/") or f.rel_path.startswith(t + ".")]
        days = compare._days([f.rel_path for f in mine])
        out[t] = {"files": len(mine), "bytes": sum(f.size or 0 for f in mine),
                  "days": [days[0], days[-1]] if days else None}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before-prefix", default="data_backup_20260831", help="earlier state, in the .env bucket")
    parser.add_argument("--after-prefix", default="data", help="current state, in the .env bucket")
    parser.add_argument("--before-source", help="earlier state as a local path or s3:// URI (overrides the prefix)")
    parser.add_argument("--after-source", help="current state as a local path or s3:// URI (overrides the prefix)")
    parser.add_argument("--work", type=Path, default=Path("data/freshness"), help="git-ignored working directory")
    parser.add_argument("--tables", nargs="*", help="silver tables (default: all but digital_events, campaign_sends)")
    parser.add_argument("--no-baseline", action="store_true", help="skip the one-load warehouse comparison")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="markdown report to write")
    parser.add_argument("--workers", type=int, default=stage.DEFAULT_WORKERS)
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_env_file(args.env_file)
    tables = args.tables or [t for t in load_contracts() if t not in LARGE_TABLES]
    try:
        before = args.before_source or _bucket_uri(args.before_prefix, dict(os.environ))
        after = args.after_source or _bucket_uri(args.after_prefix, dict(os.environ))
        labels = {"before": args.before_source or f"<bucket>/{args.before_prefix.strip('/')}/",
                  "after": args.after_source or f"<bucket>/{args.after_prefix.strip('/')}/"}
        results = run_demo(before, after, args.work, tables, baseline=not args.no_baseline, workers=args.workers)
    except (ValueError, duckdb.Error) as exc:
        print(f"freshness demo failed: {exc}", file=sys.stderr)
        return 1
    results["before_source"], results["after_source"] = labels["before"], labels["after"]  # no bucket name in files
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / "results.json").write_text(json.dumps(results, indent=2, default=_default, ensure_ascii=False),
                                            encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(json.loads(json.dumps(results, default=_default))), encoding="utf-8")
    print(f"results: {args.work / 'results.json'}")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
