"""Small, seeded, in-memory stand-in for customers and transactions (no organizer data needed)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ml.scenarios.vocab import MERCHANTS

TYPES = ["Purchase", "Purchase", "Withdrawal", "Transfer", "Payment", "Deposit", "Adjustment"]
CHANNELS = ["ATM", "App", "Web", "POS", "Branch"]
CURRENCY = {"MX": "USD", "CO": "COP", "AR": "ARS"}
CITY = {"MX": "Guadalajara", "CO": "Bogotá", "AR": "Rosario"}


def make_world(n_customers: int = 240, seed: int = 3) -> tuple[list[dict], dict[str, list[dict]]]:
    rng = random.Random(seed)
    customers, txs = [], {}
    start = datetime(2023, 7, 1)
    for i in range(n_customers):
        country = ["MX", "CO", "AR"][i % 3]
        cid = f"CLI-T{i:05d}"
        customers.append({"customer_id": cid, "country": country, "segment": ["Basic", "Plus", "Premium", "Student"][i % 4]})
        rows = []
        for j in range(rng.randint(30, 60)):
            ts = start + timedelta(days=rng.randint(0, 1080), seconds=rng.randint(0, 86399))
            typ = rng.choice(TYPES)
            base = {"USD": 300.0, "COP": 900_000.0, "ARS": 120_000.0}[CURRENCY[country]]
            rows.append({"transaction_id": f"TRX-{i:05d}-{j:03d}", "ts": ts.isoformat(sep=" "), "date": ts.date(),
                         "customer_id": cid, "transaction_type": typ,
                         "amount": round(base * rng.lognormvariate(0, 0.8), 2), "currency": CURRENCY[country],
                         "channel": rng.choice(CHANNELS),
                         "merchant_name": rng.choice(MERCHANTS) if typ == "Purchase" else None,
                         "merchant_category": None, "transaction_city": CITY[country],
                         "transaction_country": country, "transaction_status": rng.choice(["Approved"] * 8 + ["Pending", "Declined"])})
        rows.sort(key=lambda t: (t["ts"], t["transaction_id"]))
        txs[cid] = rows
    return customers, txs
