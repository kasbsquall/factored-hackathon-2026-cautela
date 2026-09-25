"""Pipeline entry point: bronze -> silver (with quarantine) for every contract table, incrementally.

    uv run python -m data_engineering.pipelines.run --source data/fixture --target data/warehouse.duckdb
    uv run python -m data_engineering.pipelines.run --source s3://bucket/prefix --tables transactions complaints

Each table is processed in its own transaction, parents before children. Only files not yet in the file ledger
are read, so a rerun on the same source changes nothing, and a file that lands late in an old partition is still
picked up. A quality report is written for every run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from data_engineering.contracts.loader import TableContract, dependency_order, load_contracts
from data_engineering.pipelines import bronze, checks, silver, warehouse
from data_engineering.pipelines.report import table_section, totals, write_report
from data_engineering.pipelines.source import (
    SourceLocation,
    connect_source,
    describe_s3_auth,
    file_schemas,
    list_files,
    parse_source,
)


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def _source_label(loc: SourceLocation) -> str | None:
    """If the source is the synthetic fixture, carry its label into the report so nobody mistakes it for real data."""
    manifest = Path(loc.root) / "manifest.json"
    if loc.kind == "local" and manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8")).get("label")
    return None


def process_table(con: duckdb.DuckDBPyConnection, loc: SourceLocation, contract: TableContract, run_id: str,
                  full_refresh: bool) -> dict:
    name = contract.table
    if full_refresh:
        warehouse.reset_table(con, name)
    discovered = list_files(con, loc, name)
    done = warehouse.loaded_files(con, name)
    new_files = [f for f in discovered if f.rel_path not in done]
    previous_hwm = warehouse.get_watermark(con, name)
    schemas = file_schemas(con, new_files) if new_files else {}
    drift = bronze.detect_drift(contract, schemas)
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    con.execute("BEGIN TRANSACTION")
    try:
        batch_columns = bronze.load_batch(con, loc, contract, new_files, schemas, run_id, ingested_at)
        per_file = bronze.rows_per_file(con)
        warehouse.ensure_silver(con, contract)
        fk_tables = checks.prepare_parent_keys(con, contract)
        checks.build_checked(con, contract, batch_columns, fk_tables)
        quarantined = checks.quarantine_failed(con, contract, run_id)
        silver.build_candidates(con, contract)
        merged = silver.merge(con, contract)
        metrics = silver.batch_metrics(con, contract, fk_tables, previous_hwm)
        uniques = silver.unique_violations(con, contract)
        hwm = silver.high_water_mark(con, contract)
        warehouse.set_watermark(con, name, hwm, run_id)
        warehouse.record_files(con, name, [(f.rel_path, per_file.get(f.rel_path, 0)) for f in new_files],
                               run_id, ingested_at)
        silver_total = con.execute(f"SELECT count(*) FROM silver.{warehouse.ident(name)}").fetchone()[0]
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    files = {"discovered": len(discovered), "new": len(new_files), "already_loaded": len(discovered) - len(new_files)}
    rows = {"bronze_in": sum(per_file.values()), "quarantined": quarantined, **merged, "silver_total": silver_total}
    return table_section(files, rows, metrics, drift, uniques, {"before": previous_hwm, "after": hwm})


def run_pipeline(source: str, target: str | Path, tables: list[str] | None = None, full_refresh: bool = False,
                 reports_dir: str | Path | None = None, env: Mapping[str, str] | None = None) -> dict:
    loc = parse_source(source)
    contracts = load_contracts()
    order = dependency_order(contracts, tables)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    reports_dir = Path(reports_dir) if reports_dir else target.parent / "reports"
    run_id = new_run_id()
    started = datetime.now(timezone.utc)
    con = duckdb.connect(str(target))
    try:
        connect_source(con, loc, env)
        warehouse.init_warehouse(con)
        con.execute("INSERT INTO control.runs VALUES (?, ?, NULL, 'running', ?, ?, NULL)",
                    [run_id, started.replace(tzinfo=None), loc.root, order])
        sections = {}
        try:
            for name in order:
                sections[name] = process_table(con, loc, contracts[name], run_id, full_refresh)
        except Exception:
            con.execute("UPDATE control.runs SET status = 'failed', finished_at = now() WHERE run_id = ?", [run_id])
            raise
        report = {
            "run_id": run_id, "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "source": loc.root, "source_kind": loc.kind, "source_label": _source_label(loc),
            "s3_auth": describe_s3_auth(env or os.environ) if loc.kind == "s3" else None,
            "target": str(target), "full_refresh": full_refresh, "tables_processed": order,
            "totals": totals(sections), "tables": sections,
        }
        path = write_report(reports_dir / f"quality_{run_id}.json", report)
        report["report_path"] = str(path)
        con.execute("UPDATE control.runs SET status = 'succeeded', finished_at = now(), report_path = ? "
                    "WHERE run_id = ?", [str(path), run_id])
        return report
    finally:
        con.close()


def _parse_tables(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    return [t for v in values for t in v.split(",") if t]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cautela bronze/silver pipeline")
    parser.add_argument("--source", required=True, help="local directory or s3://bucket/prefix")
    parser.add_argument("--target", default="data/warehouse.duckdb", help="DuckDB file to create or update")
    parser.add_argument("--tables", nargs="*", help="subset of tables (parents must already be loaded)")
    parser.add_argument("--full-refresh", action="store_true", help="drop and rebuild the selected tables")
    parser.add_argument("--reports-dir", help="where to write quality_<run_id>.json (default: next to target)")
    args = parser.parse_args(argv)
    try:
        report = run_pipeline(args.source, args.target, _parse_tables(args.tables), args.full_refresh,
                              args.reports_dir)
    except (ValueError, duckdb.Error) as exc:
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 1
    if report["source_label"]:
        print(f"source: {report['source_label']}")
    print(f"run {report['run_id']}")
    print(f"{'table':<26}{'new files':>10}{'rows in':>10}{'quarant.':>10}{'dups':>8}{'silver':>10}{'drift':>7}")
    for name, sec in report["tables"].items():
        r = sec["rows"]
        print(f"{name:<26}{sec['files']['new']:>10}{r['bronze_in']:>10}{r['quarantined']:>10}"
              f"{r['exact_duplicates']:>8}{r['silver_total']:>10}{len(sec['drift_events']):>7}")
    print(f"report: {report['report_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
