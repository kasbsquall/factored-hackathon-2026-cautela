"""Pick demo customers and charges from a warehouse, and write the customer's opening message for each scenario.

Used by the CLI demo (agent/demo.py), the API seed (api/seed.py) and the orchestrator tests. Queries are read-only.
The messages are team-written templates filled with the charge's own amount, merchant and date; Portuguese is
team-generated (the supplied data has no Brazil and no Portuguese).

Scenarios:
  normal      approved purchase under the USD review threshold, no fraud signal, inside the Mexico window
  human       approved purchase at or above the USD 450 review threshold (SYN-AMOUNT-001)
  ambiguous   a vague description ("a purchase last week") that fits several charges
  bad_data    approved purchase in local currency with no USD amount; valued with the fixed rates of SYN-FX-001
  declined    a declined transaction: nothing to dispute (SYN-STATUS-002)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb

from ml.features.parse import parse_description

MONTHS = {"es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre"],
          "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
                 "outubro", "novembro", "dezembro"]}
OPENERS = {
    "es": "No reconozco un cargo de {amount} {currency} en {merchant} el {day} de {month}.",
    "pt": "Não reconheço uma compra de {amount} {currency} na {merchant} no dia {day} de {month}.",
}
VAGUE = {"es": "Hay una compra de la semana pasada que no reconozco.",
         "pt": "Tem uma compra da semana passada que eu não reconheço."}

_BASE = """
WITH people AS (
    SELECT c.customer_id, c.document_number, c.country, c.first_name FROM silver.customers c
    WHERE c.customer_status = 'Active' AND c.mobile_phone IS NOT NULL
      AND c.document_number IN (SELECT document_number FROM silver.customers GROUP BY 1 HAVING count(*) = 1)
)
SELECT p.customer_id, p.document_number, p.country, t.transaction_id, t.transaction_date, t.amount, t.currency,
       t.amount_usd, t.merchant_name, t.transaction_status
FROM people p JOIN silver.transactions t USING (customer_id)
WHERE t.transaction_type = 'Purchase' AND t.merchant_name IS NOT NULL
  AND t.transaction_date BETWEEN ? AND ? AND {where}
  AND NOT EXISTS (SELECT 1 FROM silver.transactions o WHERE o.customer_id = t.customer_id
                  AND o.transaction_id <> t.transaction_id AND CAST(o.transaction_date AS DATE) = CAST(t.transaction_date AS DATE))
ORDER BY t.transaction_date DESC, t.transaction_id
LIMIT 1
"""
WHERE = {
    "normal": "p.country = 'Mexico' AND t.transaction_status = 'Approved' AND t.amount_usd < 450 "
              "AND coalesce(t.fraud_score, 0) < 50 AND NOT coalesce(t.is_fraud, false)",
    "human": "p.country = 'Mexico' AND t.transaction_status = 'Approved' AND t.amount_usd >= 450 "
             "AND coalesce(t.fraud_score, 0) < 50 AND NOT coalesce(t.is_fraud, false)",
    "bad_data": "p.country = 'Mexico' AND t.transaction_status = 'Approved' AND t.amount_usd IS NULL "
                "AND t.currency <> 'USD' AND coalesce(t.fraud_score, 0) < 50 AND NOT coalesce(t.is_fraud, false)",
    "declined": "t.transaction_status = 'Declined'",
    "ambiguous": "p.country = 'Mexico' AND t.transaction_status = 'Approved' AND t.amount_usd < 450 "
                 "AND coalesce(t.fraud_score, 0) < 50 "
                 "AND (SELECT count(*) FROM silver.transactions w WHERE w.customer_id = t.customer_id "
                 "AND w.transaction_type = 'Purchase' AND w.transaction_date BETWEEN ? AND ?) >= 3",
}
SCENARIOS = tuple(WHERE)


@dataclass(frozen=True)
class DemoCase:
    scenario: str
    customer_id: str
    document_number: str
    country: str
    transaction_id: str
    transaction: dict[str, Any]

    def opener(self, lang: str) -> str:
        if self.scenario == "ambiguous":
            return VAGUE[lang]
        when: datetime = self.transaction["transaction_date"]
        return OPENERS[lang].format(amount=f"{float(self.transaction['amount']):.2f}",
                                    currency=self.transaction["currency"], merchant=self.transaction["merchant_name"],
                                    day=when.day, month=MONTHS[lang][when.month - 1])


def default_as_of(warehouse: str | Path) -> datetime:
    """The data is static: the demo clock starts at noon UTC the day after the last transaction."""
    with duckdb.connect(str(warehouse), read_only=True) as con:
        last = con.execute("SELECT max(transaction_date) FROM silver.transactions").fetchone()[0]
    if last is None:
        raise ValueError("the warehouse has no transactions")
    return datetime(last.year, last.month, last.day, 12, tzinfo=UTC) + timedelta(days=1)


def find_case(warehouse: str | Path, scenario: str, as_of: datetime, exclude: tuple[str, ...] = ()) -> DemoCase | None:
    """The newest charge that fits the scenario, for a customer not in `exclude`."""
    as_of = as_of.replace(tzinfo=None)
    start, end = as_of - timedelta(days=25), as_of
    params: list[Any] = []
    if scenario == "ambiguous":  # the week the vague opener refers to, as the deterministic parser reads it
        parsed = parse_description(VAGUE["es"], as_of.date())
        start, end = datetime.combine(parsed.date_lo, time.min), datetime.combine(parsed.date_hi, time.max)
        params = [start, end]
    params = [start, end, *params]
    where = WHERE[scenario]
    if exclude:
        where += f" AND p.customer_id NOT IN ({', '.join('?' for _ in exclude)})"
        params += list(exclude)
    with duckdb.connect(str(warehouse), read_only=True) as con:  # placeholders: BETWEEN, then `where` in order
        cur = con.execute(_BASE.format(where=where), params)
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
    if row is None:
        return None
    data = dict(zip(names, row, strict=True))
    return DemoCase(scenario, data["customer_id"], data["document_number"], data["country"], data["transaction_id"],
                    data)


def find_all(warehouse: str | Path, as_of: datetime) -> dict[str, DemoCase]:
    """One case per scenario, each for a different customer where the data allows it."""
    out: dict[str, DemoCase] = {}
    for scenario in SCENARIOS:
        case = find_case(warehouse, scenario, as_of, tuple(c.customer_id for c in out.values()))
        case = case or find_case(warehouse, scenario, as_of)
        if case is not None:
            out[scenario] = case
    return out
