"""Clean row builders for transactions and complaints, the two tables the dispute workflow depends on.

Dispute-type complaints are tied to a real fixture transaction (same customer, product, amount and currency,
created after the charge). The dictionary has no complaints.transaction_id column, so the link is recorded as
ground truth in manifest.json for later tests of the linking logic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from data_engineering.fixtures import vocab
from data_engineering.fixtures.dimensions import SIZES, Ctx

SLA_DAYS = 15  # Fixture assumption. The real SLA threshold behind sla_breached is not documented.

_CHANNEL_TYPES = [
    ("POS", "Purchase", 40), ("App", "Purchase", 10), ("Web", "Purchase", 10), ("App", "Payment", 10),
    ("Web", "Payment", 5), ("ATM", "Withdrawal", 10), ("Branch", "Deposit", 5), ("Transfer", "Transfer", 9),
    ("Branch", "Adjustment", 1),
]
_STATUS = [("Approved", 90), ("Declined", 5), ("Pending", 3), ("Reversed", 2)]
_RESPONSE = {"Approved": "00", "Declined": "05", "Pending": "09", "Reversed": "00"}


def event_and_process(ctx: Ctx, day: date | None = None) -> tuple[datetime, date]:
    """Normal delivery: processed the same day or the next day, never later (lag <= 1 day)."""
    event = ctx.moment(day or ctx.day())
    process = min(event.date() + timedelta(days=ctx.rng.choice([0, 0, 1])), ctx.end)
    return event, process


def _weighted(ctx: Ctx, options: list[tuple]) -> tuple:
    return ctx.rng.choices(options, weights=[o[-1] for o in options])[0]


def build_transactions(ctx: Ctx, customers: list[dict], products: list[dict], branches: list[dict],
                       rates: list[dict]) -> list[dict]:
    rate = {(r["date"], r["source_currency"]): r["exchange_rate"] for r in rates}
    country_of = {c["customer_id"]: c["country"] for c in customers}
    branches_by_country: dict[str, list[str]] = {}
    for b in branches:
        branches_by_country.setdefault(b["country"], []).append(b["branch_id"])
    rows = []
    for i in range(SIZES["transactions"]):
        product = ctx.rng.choice(products)
        country = country_of[product["customer_id"]]
        channel, kind, _ = _weighted(ctx, _CHANNEL_TYPES)
        category = ctx.rng.choice(list(vocab.MERCHANTS))
        merchant, mcc = ctx.rng.choice(vocab.MERCHANTS[category]) if kind in ("Purchase", "Payment") else (None, None)
        status = _weighted(ctx, _STATUS)[0]
        is_fraud = kind == "Purchase" and ctx.rng.random() < 0.01
        abroad = is_fraud and ctx.rng.random() < 0.5
        event, process = event_and_process(ctx)
        amount = ctx.money(5, 400) if product["currency"] == "USD" else ctx.money(50, 25_000)
        usd = 1.0 if product["currency"] == "USD" else rate[(event.date(), product["currency"])]
        city = ctx.rng.choice(vocab.COUNTRIES[country]["cities"])[0]
        rows.append({
            "transaction_id": f"TX{i + 1:08d}", "transaction_date": event, "process_date": process,
            "product_id": product["product_id"], "customer_id": product["customer_id"], "transaction_type": kind,
            "transaction_category": category if merchant else "Other", "amount": amount,
            "currency": product["currency"], "amount_usd": round(amount * usd, 2), "channel": channel,
            "branch_id": ctx.rng.choice(branches_by_country[country]) if channel in ("Branch", "ATM") else None,
            "merchant_name": merchant, "merchant_category": mcc,
            "transaction_country": "United States" if abroad else country,
            "transaction_city": "Miami" if abroad else city, "transaction_status": status,
            "response_code": _RESPONSE[status], "is_fraud": is_fraud,
            "fraud_score": round(ctx.rng.uniform(70, 99), 2) if is_fraud else round(ctx.rng.uniform(0, 40), 2),
            "latitude": round(ctx.rng.uniform(-35, 25), 7), "longitude": round(ctx.rng.uniform(-100, -58), 7),
        })
    return rows


def _dispute_text(ctx: Ctx, category: str, tx: dict, accent: str) -> str:
    opener = vocab.ACCENT_OPENERS.get(accent, "Buenas tardes,")
    charge = f"{tx['amount']:.2f} {tx['currency']} en {tx['merchant_name']} del {tx['transaction_date']:%d/%m/%Y}"
    bodies = {
        "Cargo no reconocido": f"no reconozco el cargo por {charge}. Solicito la devolución.",
        "Cobro duplicado": f"me cobraron dos veces la compra por {charge}. Pido que reversen el cobro repetido.",
        "Fraude con tarjeta": f"aparece una compra por {charge} que yo no hice. Creo que clonaron mi tarjeta.",
        "Compra no recibida": f"pagué {charge} y el comercio nunca me entregó el producto.",
    }
    return f"{opener} {bodies[category]}"


def _lifecycle(ctx: Ctx, created: datetime) -> dict:
    status = _weighted(ctx, [("Open", 25), ("In Process", 20), ("Escalated", 10), ("Resolved", 25),
                             ("Closed", 10), ("Rejected", 10)])[0]
    days = ctx.rng.randint(1, 30)
    resolved_at = created + timedelta(days=days)
    if status in vocab.RESOLUTIONS and resolved_at.date() > ctx.end:
        status = "In Process"
    finished = status in vocab.RESOLUTIONS
    age = (resolved_at.date() - created.date()).days if finished else (ctx.end - created.date()).days
    return {
        "status": status, "assignment_date": None if status == "Open" else created + timedelta(hours=4),
        "first_response_date": None if status == "Open" else created + timedelta(days=1),
        "resolution_date": resolved_at if finished else None,
        "closing_date": resolved_at if status == "Closed" else None,
        "sla_breached": age > SLA_DAYS, "resolution_days": days if finished else None,
        "resolution": vocab.RESOLUTIONS.get(status),
        "resolution_satisfaction": ctx.rng.randint(1, 5) if finished else None,
    }


def build_complaints(ctx: Ctx, customers: list[dict], transactions: list[dict], agents: list[dict],
                     interactions: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Return complaint rows and the ground-truth map complaint_id -> disputed transaction_id."""
    accent_of = {c["customer_id"]: c["detected_accent"] for c in customers}
    branch_of = {c["customer_id"]: c["registration_branch_id"] for c in customers}
    interactions_of: dict[str, list[str]] = {}
    for it in interactions:
        interactions_of.setdefault(it["customer_id"], []).append(it["interaction_id"])
    disputable = [t for t in transactions if t["transaction_type"] == "Purchase" and t["transaction_status"] == "Approved"
                  and t["transaction_date"].date() < ctx.end]
    frauds = [t for t in disputable if t["is_fraud"]] or disputable
    rows, links = [], {}
    for i in range(SIZES["complaints"]):
        complaint_id = f"CL{i + 1:07d}"
        is_dispute = ctx.rng.random() < 0.45
        if is_dispute:
            category, subcategory = ctx.rng.choice(vocab.DISPUTE_CATEGORIES)
            tx = ctx.rng.choice(frauds if category == "Fraude con tarjeta" else disputable)
            customer_id = tx["customer_id"]
            created, _ = event_and_process(ctx, min(tx["transaction_date"].date() + timedelta(days=ctx.rng.randint(1, 15)), ctx.end))
            links[complaint_id] = tx["transaction_id"]
        else:
            category, subcategory = ctx.rng.choice(vocab.OTHER_CATEGORIES)
            tx, customer_id = None, ctx.rng.choice(customers)["customer_id"]
            created, _ = event_and_process(ctx)
        process = min(created.date() + timedelta(days=ctx.rng.choice([0, 1])), ctx.end)
        channel = ctx.rng.choice(["Call Center", "Call Center", "App", "Web", "Email", "Branch", "Regulator"])
        origin = interactions_of.get(customer_id) if channel == "Call Center" else None
        life = _lifecycle(ctx, created)
        text = (_dispute_text(ctx, category, tx, accent_of[customer_id]) if tx
                else f"Presento un reclamo por {category.lower()}: {subcategory.lower()}.")
        rows.append({
            "complaint_id": complaint_id, "creation_date": created, "process_date": process, "customer_id": customer_id,
            "case_type": "Claim" if is_dispute else ctx.rng.choice(["Complaint", "Request", "Suggestion"]),
            "category": category, "subcategory": subcategory, "reception_channel": channel,
            "affected_product_id": tx["product_id"] if tx else None,
            "related_branch_id": branch_of[customer_id] if channel == "Branch" else None,
            "origin_interaction_id": ctx.rng.choice(origin) if origin else None, "description": text,
            "claimed_amount": tx["amount"] if tx else None, "currency": tx["currency"] if tx else None,
            "priority": ctx.rng.choice(["Low", "Medium", "High", "Critical"] if is_dispute else ["Low", "Medium"]),
            "assigned_agent_id": None if life["status"] == "Open" else ctx.rng.choice(agents)["agent_id"],
            **{k: v for k, v in life.items()},
            "compensation_granted": tx["amount"] if tx and life["status"] == "Resolved" else None,
            "is_repeat_complainer": ctx.rng.random() < 0.15,
        })
    return rows, links


def resolve_complaint(ctx: Ctx, row: dict, when: date) -> dict:
    """A later version of an open complaint: it is resolved and re-delivered in a later partition."""
    resolved_at = datetime.combine(when, datetime.min.time()) + timedelta(hours=ctx.rng.randint(8, 18))
    days = (resolved_at.date() - row["creation_date"].date()).days
    return {**row, "process_date": when, "status": "Resolved", "resolution_date": resolved_at,
            "resolution_days": days, "sla_breached": days > SLA_DAYS, "resolution": vocab.RESOLUTIONS["Resolved"],
            "assignment_date": row["assignment_date"] or row["creation_date"] + timedelta(hours=4),
            "first_response_date": row["first_response_date"] or row["creation_date"] + timedelta(days=1),
            "resolution_satisfaction": ctx.rng.randint(1, 5)}
