"""Gold entry point: silver -> gold for every gold contract, incrementally.

    uv run python -m data_engineering.gold.run --target data/warehouse.duckdb
    uv run python -m data_engineering.gold.run --target data/warehouse_real.duckdb --tables complaint_facts

Writes data/reports/gold_<run_id>.json (next to the warehouse) with the mode, row counts and every check result
per table. A rerun on unchanged silver skips every materialized table.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from data_engineering.gold.build import build_gold, init_gold
from data_engineering.gold.checks import GoldQualityError
from data_engineering.gold.contract import load_gold_contracts
from data_engineering.pipelines.report import write_report
from data_engineering.pipelines.run import new_run_id


def run_gold(target: str | Path, tables: list[str] | None = None, reports_dir: str | Path | None = None) -> dict:
    target = Path(target)
    if not target.exists():
        raise ValueError(f"{target} does not exist; run the bronze/silver pipeline first")
    reports_dir = Path(reports_dir) if reports_dir else target.parent / "reports"
    contracts = load_gold_contracts()
    run_id = new_run_id()
    started = datetime.now(timezone.utc)
    con = duckdb.connect(str(target))
    try:
        init_gold(con)
        con.execute("INSERT INTO control.gold_runs VALUES (?, ?, NULL, 'running', ?, NULL)",
                    [run_id, started.replace(tzinfo=None), tables or sorted(contracts)])
        try:
            sections = build_gold(con, contracts, run_id, tables)
        except Exception:
            con.execute("UPDATE control.gold_runs SET status = 'failed', finished_at = now() WHERE run_id = ?",
                        [run_id])
            raise
        report = {"run_id": run_id, "started_at": started.isoformat(),
                  "finished_at": datetime.now(timezone.utc).isoformat(), "target": str(target),
                  "silver_runs": [r[0] for r in con.execute(
                      "SELECT run_id FROM control.runs WHERE status = 'succeeded' ORDER BY started_at").fetchall()],
                  "tables": sections}
        path = write_report(reports_dir / f"gold_{run_id}.json", report)
        report["report_path"] = str(path)
        con.execute("UPDATE control.gold_runs SET status = 'succeeded', finished_at = now(), report_path = ? "
                    "WHERE run_id = ?", [str(path), run_id])
        return report
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cautela gold layer")
    parser.add_argument("--target", default="data/warehouse.duckdb", help="warehouse with bronze/silver loaded")
    parser.add_argument("--tables", nargs="*", help="subset of gold tables (their gold inputs must exist)")
    parser.add_argument("--reports-dir", help="where to write gold_<run_id>.json (default: next to target)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = run_gold(args.target, args.tables, args.reports_dir)
    except (ValueError, duckdb.Error, GoldQualityError) as exc:
        print(f"gold failed: {exc}", file=sys.stderr)
        return 1
    print(f"gold run {report['run_id']}")
    print(f"{'table':<24}{'kind':>10}{'mode':>13}{'changed':>10}{'inserted':>10}{'total':>10}{'checks':>8}")
    for name, sec in report["tables"].items():
        passed = sum(1 for c in sec["checks"] if not c["violations"])
        print(f"{name:<24}{sec['kind']:>10}{sec['mode']:>13}{str(sec['keys_changed'] or '-'):>10}"
              f"{str(sec['rows_inserted'] if sec['rows_inserted'] is not None else '-'):>10}{sec['rows_total']:>10}"
              f"{passed:>4}/{len(sec['checks']):<3}")
    print(f"report: {report['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
