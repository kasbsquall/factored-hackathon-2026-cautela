"""Build the workflow evidence from the gold layer: chart JSON files and why-this-workflow.md.

    uv run python -m data_analytics.run --warehouse data/warehouse_real.duckdb
    uv run python -m data_analytics.run --warehouse data/warehouse.duckdb --out <dir>   # fixture

Reads the warehouse read-only. The gold layer must be built first (`make gold`). A fixture warehouse cannot write
to the committed reports directory, so test numbers never replace the organizer-data report.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from data_analytics import figures
from data_analytics.cost_model import cost_model, ml_rates
from data_analytics.data_limits import diagnostics
from data_analytics.render import render_report

DEFAULT_OUT = Path(__file__).resolve().parent / "reports"
DEFAULT_ML = Path("ml/reports/results.json")


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def provenance(con: duckdb.DuckDBPyConnection, warehouse: Path) -> dict:
    silver = [r[0] for r in con.execute(
        "SELECT run_id FROM control.runs WHERE status = 'succeeded' ORDER BY started_at").fetchall()]
    gold = con.execute("SELECT run_id FROM control.gold_runs WHERE status = 'succeeded' "
                       "ORDER BY started_at DESC LIMIT 1").fetchone()
    source = con.execute("SELECT source FROM control.runs WHERE status = 'succeeded' LIMIT 1").fetchone()
    return {"warehouse": warehouse.name, "source_is_fixture": bool(source and "fixture" in source[0]),
            "silver_runs": silver, "latest_gold_run": gold[0] if gold else None}


def collect(warehouse: Path, ml_results: Path) -> dict:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        profile = figures.dispute_profile(con)
        categories = figures.by_reason_category(con)
        ml = ml_rates(ml_results)
        return {
            "provenance": provenance(con, warehouse),
            "workflows": figures.workflow_table(con),
            "dispute_profile": profile,
            "fcr_by_reason": figures.fcr_by_reason(con),
            "fcr_by_reason_category": categories,
            "interaction_channel_mix": figures.interaction_channel_mix(con),
            "breakdowns": figures.breakdowns(con),
            "demand": figures.demand(con),
            "data_limits": diagnostics(con),
            "ml": ml,
            "cost_model": cost_model(profile, categories, ml),
        }
    finally:
        con.close()


def _chart(title: str, unit: str, denominator: str, data, prov: dict) -> dict:
    return {"title": title, "unit": unit, "denominator": denominator, "provenance": prov, "data": data}


def chart_files(ev: dict) -> dict[str, dict]:
    """One JSON per chart for the frontend and the deck. Each states its unit and denominator."""
    p, d, dem = ev["provenance"], ev["dispute_profile"], ev["demand"]
    n = d["complaints"]
    return {
        "workflow_comparison.json": _chart(
            "Complaint types: volume, SLA breach, escalation, resolution time, repeat contact", "complaints",
            "all complaints (share); complaints of the type (rates)", ev["workflows"], p),
        "fcr_by_contact_reason.json": _chart(
            "First-contact resolution by contact reason (contact center)", "share of interactions",
            "interactions with a non-null was_resolved", ev["fcr_by_reason"], p),
        "dispute_channel_mix.json": _chart("Unrecognized-charge complaints by reception channel", "complaints",
                                           f"{n} unrecognized-charge complaints", d["channel_mix"], p),
        "interaction_channel_mix.json": _chart("Contact-center interactions by channel", "interactions",
                                               "all interactions", ev["interaction_channel_mix"], p),
        "demand_by_hour.json": _chart(
            "Contacts by hour of day (as stored, timezone undocumented)", "contacts", "all contacts of the series",
            {"disputes": dem["disputes_by_hour"], "interactions": dem["interactions_by_hour"]}, p),
        "demand_by_weekday.json": _chart(
            "Contacts by ISO weekday (1 = Monday)", "contacts", "all contacts of the series",
            {"disputes": dem["disputes_by_weekday"], "interactions": dem["interactions_by_weekday"]}, p),
        "demand_daily.json": _chart(
            "Contacts per calendar day, zero days included", "contacts per day", "calendar days in the window",
            {"disputes": dem["disputes_daily"], "disputes_by_country": dem["disputes_daily_by_country"],
             "interactions": dem["interactions_daily"]}, p),
        "dispute_breakdowns.json": _chart(
            "Unrecognized-charge complaints by country and segment", "complaints",
            "complaints in the group; customers in the group for the per-1,000 rate", ev["breakdowns"], p),
        "cost_model.json": _chart("Cost per resolution and automation savings (projection)", "hours and USD",
                                  "see inputs", ev["cost_model"], p),
        "data_limits.json": _chart("Where the supplied data is uniform, templated or unlinkable", "various",
                                   "see each entry", ev["data_limits"], p),
    }


def write_outputs(ev: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in chart_files(ev).items():
        path = out / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
                        encoding="utf-8")
        written.append(path)
    report = out / "why-this-workflow.md"
    report.write_text(render_report(ev), encoding="utf-8")
    written.append(report)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cautela workflow evidence")
    parser.add_argument("--warehouse", required=True, help="warehouse with gold built")
    parser.add_argument("--ml-results", default=str(DEFAULT_ML), help="ml/reports/results.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    warehouse = Path(args.warehouse)
    if not warehouse.exists():
        print(f"analytics failed: {warehouse} does not exist; run the pipeline and `make gold` first",
              file=sys.stderr)
        return 1
    try:
        ev = collect(warehouse, Path(args.ml_results))
    except (duckdb.Error, FileNotFoundError, KeyError) as exc:
        print(f"analytics failed: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    if ev["provenance"]["source_is_fixture"] and out.resolve() == DEFAULT_OUT:
        print("analytics refused: the warehouse holds the synthetic fixture; pass --out to write elsewhere",
              file=sys.stderr)
        return 1
    for path in write_outputs(ev, out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
