"""Deterministic synthetic test fixture for the data pipeline.

    uv run python -m data_engineering.fixtures.generate --out data/fixture --seed 42

SYNTHETIC TEST FIXTURE, team-generated, not organizer data. It exists so the pipeline can be built and tested
before the organizer data arrives, and so update correctness (late arrivals, re-delivered versions) can be proven
with static files, as the problem statement allows. Shapes follow the data contracts; sizes are roughly the
organizer row counts divided by 250; issue rates follow the dictionary (~2% duplicates, ~5% nulls in nullable
fields). manifest.json records every injected issue and is the ground truth for the tests.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from data_engineering.contracts.loader import load_contracts
from data_engineering.fixtures import dimensions as dims
from data_engineering.fixtures import facts_contact as contact
from data_engineering.fixtures import facts_money as money
from data_engineering.fixtures.issues import DUPLICATE_RATE, NULL_RATE, Injector
from data_engineering.fixtures.writer import write_table

LABEL = "SYNTHETIC TEST FIXTURE, team-generated, not organizer data"
TYPE_DRIFT_DAY = 50  # transactions partition (day offset) written with `amount` as text, including bad values
EVOLUTION_DAY = 45  # from this transactions partition on, an extra `installments` column appears


def _plan_customers(inj: Injector, ctx: dims.Ctx) -> None:
    inj.null_pk(2)
    inj.set_value("bad_enum", "segment", "Gold", 2)
    inj.set_value("out_of_range", "credit_score", 920, 3)
    inj.duplicate_unique("document_number", 2)


def _plan_transactions(inj: Injector, ctx: dims.Ctx) -> None:
    drift_day = ctx.start + timedelta(days=TYPE_DRIFT_DAY)
    inj.null_pk(5)
    inj.set_value("bad_enum", "transaction_status", "Aprobada", 8)
    inj.set_value("bad_enum", "transaction_type", "Compra", 3)  # decoded enum: booked as a warning
    inj.set_value("out_of_range", "fraud_score", 150.0, 4)
    inj.orphan("customer_id", 6)
    inj.orphan("product_id", 5)
    inj.set_value("null_required", "transaction_country", None, 3)
    inj.set_value("type_cast_error", "amount", lambda k: f"{k + 1}.{234 + k:03d},56", 6,
                  where=lambda r: r["process_date"] == drift_day)
    inj.late_rows(100)
    inj.late_updates(40, where=lambda r: r["transaction_status"] == "Pending",
                     change=lambda r, when: {**r, "transaction_status": ctx.rng.choice(["Approved", "Reversed"])},
                     track=["transaction_status"])


def _plan_complaints(inj: Injector, ctx: dims.Ctx) -> None:
    inj.null_pk(2)
    inj.set_value("bad_enum", "priority", "Alta", 3)
    inj.set_value("bad_enum", "status", "Abierto", 3)  # decoded enum: booked as a warning
    inj.orphan("customer_id", 2)
    inj.late_rows(10, where=lambda r: r["claimed_amount"] is None)
    inj.late_updates(30, where=lambda r: r["status"] in ("Open", "In Process"),
                     change=lambda r, when: money.resolve_complaint(ctx, r, when), track=["status", "sla_breached"])


PLANS = {
    "customers": _plan_customers,
    "products": lambda inj, ctx: inj.orphan("customer_id", 3),
    "transactions": _plan_transactions,
    "call_center_interactions": lambda inj, ctx: (
        inj.set_value("out_of_range", "sentiment_score", 1.8, 3), inj.orphan("agent_id", 4), inj.late_rows(20)),
    "satisfaction_surveys": lambda inj, ctx: inj.set_value("out_of_range", "question_1_response", 7, 2),
    "digital_events": lambda inj, ctx: (inj.orphan("customer_id", 10), inj.late_rows(100)),
    "complaints": _plan_complaints,
    "campaign_sends": lambda inj, ctx: inj.orphan("campaign_id", 3),
}


def _clean_rows(ctx: dims.Ctx) -> tuple[dict[str, list[dict]], dict[str, str]]:
    branches = dims.build_branches(ctx)
    customers = dims.build_customers(ctx, branches)
    agents = dims.build_agents(ctx, branches)
    campaigns = dims.build_campaigns(ctx)
    products = dims.build_products(ctx, customers)
    rates = dims.build_exchange_rates(ctx)
    transactions = money.build_transactions(ctx, customers, products, branches, rates)
    interactions = contact.build_interactions(ctx, customers, agents)
    complaints, links = money.build_complaints(ctx, customers, transactions, agents, interactions)
    tables = {
        "branches": branches, "customers": customers, "service_agents": agents, "marketing_campaigns": campaigns,
        "products": products, "daily_exchange_rates": rates, "transactions": transactions,
        "call_center_interactions": interactions, "call_transcripts": contact.build_transcripts(ctx, interactions),
        "satisfaction_surveys": contact.build_surveys(ctx, interactions), "complaints": complaints,
        "digital_events": contact.build_digital_events(ctx, customers, products),
        "campaign_sends": contact.build_campaign_sends(ctx, customers, campaigns),
    }
    return tables, links


def _apply_file_level_drift(ctx: dims.Ctx, rows: list[dict]) -> None:
    """Type drift (amount as text in one partition) and schema evolution (new column in later partitions)."""
    drift_day = ctx.start + timedelta(days=TYPE_DRIFT_DAY)
    evolution_day = ctx.start + timedelta(days=EVOLUTION_DAY)
    for row in rows:
        if row["process_date"] == drift_day and not isinstance(row["amount"], str):
            row["amount"] = f"{row['amount']:.2f}"
        if row["process_date"] >= evolution_day:
            tx_id = row["transaction_id"] or "0"
            row["installments"] = int(tx_id[-2:]) % 12 + 1


def _prepare_output(out: Path) -> None:
    manifest = out / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text(encoding="utf-8")).get("label") == LABEL:
        shutil.rmtree(out)
    elif out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty and is not a previous fixture; refusing to overwrite it")
    out.mkdir(parents=True, exist_ok=True)


def generate(out: Path, seed: int = 42) -> dict:
    contracts = load_contracts()
    ctx = dims.Ctx(seed)
    snapshot = ctx.end + timedelta(days=1)
    tables, links = _clean_rows(ctx)
    _prepare_output(out)
    manifest_tables, injectors, final_rows = {}, {}, {}
    for name in sorted(tables):
        inj = Injector(ctx, contracts[name], tables[name])
        inj.nulls()
        PLANS.get(name, lambda i, c: None)(inj, ctx)
        inj.duplicates()
        injectors[name], final_rows[name] = inj, inj.finish()
    for name in sorted(tables):
        contract, inj, rows = contracts[name], injectors[name], final_rows[name]
        inj.count_parent_quarantined(rows, injectors)
        drift = {}
        if name == "transactions":
            _apply_file_level_drift(ctx, rows)
        files = write_table(out, contract, rows, snapshot)
        if name == "transactions":
            evolved = [f for f in files if _file_day(f) >= ctx.start + timedelta(days=EVOLUTION_DAY)]
            drift = {"unexpected_column": {"installments": len(evolved)}, "type_changed": {"amount": 1}}
        ledger = inj.ledger.as_dict()
        manifest_tables[name] = {
            "rows_written": len(rows), "files": len(files), "drift": drift, **ledger,
            "expected_silver_rows": len(rows) - sum(ledger["quarantine"].values())
            - ledger["exact_duplicates"] - ledger["late_updates"],
        }
    manifest = {
        "label": LABEL, "seed": seed, "generator": "data_engineering/fixtures/generate.py",
        "process_date_range": [ctx.start.isoformat(), ctx.end.isoformat()], "snapshot_date": snapshot.isoformat(),
        "rates": {"duplicate_rate": DUPLICATE_RATE, "null_rate_nullable_columns": NULL_RATE},
        "notes": [
            "All values are invented test data. Category and reason labels are plausible Spanish wording, "
            "not the organizer vocabulary.",
            "dispute_links maps a dispute complaint to the fixture transaction it refers to (ground truth for "
            "future linking tests; the dictionary has no complaints.transaction_id).",
            "complaints.sla_breached uses a 15-day SLA, a fixture assumption.",
        ],
        "tables": manifest_tables,
        "dispute_links": dict(sorted(links.items())),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False),
                                       encoding="utf-8")
    return manifest


def _file_day(rel_path: str) -> date:
    parts = dict(p.split("=") for p in rel_path.split("/") if "=" in p)
    return date(int(parts["year"]), int(parts["month"]), int(parts["day"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/fixture"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    manifest = generate(args.out, args.seed)
    total = sum(t["rows_written"] for t in manifest["tables"].values())
    print(f"{LABEL}: wrote {total} rows across {len(manifest['tables'])} tables to {args.out} (seed {args.seed})")


if __name__ == "__main__":
    main()
