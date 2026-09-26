"""Gold outcome for one disputed charge, from the written policy (agent/policy/rules.yaml), computed independently.

This is the reference the end-to-end evaluation scores against, so it deliberately does not call
agent.policy.engine: it reads the same rule values (windows, threshold, fraud level, FX rates, reason priority)
from rules.yaml and applies them with its own code. A disagreement between this oracle and the service is either a
service bug or an oracle bug, and either way it shows up in the report instead of cancelling out.

What the oracle answers for a charge the customer really means (the case's target transaction):
  resolved          the dispute can be opened automatically: expected final state is a verified open case
  handoff + case    the write is allowed but policy escalates (amount, fraud, unknown USD amount): a case is
                    stored as pending review and the conversation transfers with that reason
  handoff, no case  outside the claim window or data missing: transfer with policy_requires_review, no write
  not_disputable    declined or reversed: explained, nothing written
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES_PATH = Path(__file__).resolve().parents[1] / "agent" / "policy" / "rules.yaml"
# Priority of reason codes when several apply (docs/schemas/handoff.schema.json order of severity, as written in
# the policy documentation): security, fraud, human request, scope, amount, review, confidence, tool.
PRIORITY = ("security_event", "suspected_fraud", "customer_requested_human", "out_of_scope",
            "amount_above_threshold", "policy_requires_review", "low_confidence", "tool_failure")
COUNTRY = {"mexico": "Mexico", "colombia": "Colombia", "argentina": "Argentina"}


@dataclass(frozen=True)
class Gold:
    kind: str                     # resolved | handoff | not_disputable
    reason: str | None = None     # handoff reason code
    expects_case: bool = False    # a case is written (status open for resolved, pending review for handoff)
    rule_basis: tuple[str, ...] = ()
    usd: float | None = None
    window_deadline: str | None = None


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def _country(value: str | None) -> str | None:
    if not value:
        return None
    plain = "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")
    return COUNTRY.get(plain.strip().lower())


def _deadline(start: date, days: int, calendar: str) -> date:
    if calendar != "business":
        return start + timedelta(days=days)
    out, n = start, 0
    while n < days:
        out += timedelta(days=1)
        n += out.weekday() < 5
    return out


def _window(country: str, product: str | None, channel: str | None) -> dict | None:
    for w in rules()["claim_windows"]:
        if w["country"] != country:
            continue
        if w["products"] != "any" and product not in w["products"]:
            continue
        if w["channels"] != "any" and channel not in w["channels"]:
            continue
        return w
    return None


def usd_value(facts: dict[str, Any]) -> float | None:
    if facts.get("amount_usd") is not None:
        return float(facts["amount_usd"])
    if facts.get("currency") == "USD" and facts.get("amount") is not None:
        return float(facts["amount"])
    rate = rules()["fx_rates"]["units_per_usd"].get(facts.get("currency") or "")
    if rate is None or facts.get("amount") is None:
        return None
    return round(float(facts["amount"]) / float(rate["rate"]), 2)


def gold_for_charge(facts: dict[str, Any], as_of: datetime) -> Gold:
    """`facts`: one row of gold.dispute_policy_inputs (the charge as the bank holds it)."""
    r = rules()
    needed = ("transaction_date", "amount", "currency", "transaction_status")
    country = _country(facts.get("customer_country"))
    when = facts.get("transaction_date")
    if any(facts.get(k) is None for k in needed) or country is None or when.date() > as_of.date():
        return Gold("handoff", "policy_requires_review", False, (r["missing_data"]["id"],))
    status = facts["transaction_status"]
    if status not in r["disputable_status"][0]["statuses"]:
        known = {s for rule in r["disputable_status"] for s in rule["statuses"]}
        if status in known:
            return Gold("not_disputable", None, False, ("status:" + status,))
        return Gold("handoff", "policy_requires_review", False, (r["missing_data"]["id"],))
    window = _window(country, facts.get("product_type"), facts.get("channel"))
    if window is None:
        return Gold("handoff", "policy_requires_review", False, (r["missing_data"]["id"],))
    deadline = _deadline(when.date(), window["days"], window["calendar"])
    inside = as_of.date() <= deadline
    # Outside the window nothing can be written, but the other reasons still apply and the most severe one names
    # the transfer (PRIORITY): a USD 900 charge past its window transfers as amount_above_threshold.
    reasons, basis = ([] if inside else ["policy_requires_review"]), [window["id"]]
    usd = usd_value(facts)
    if usd is None:
        reasons.append("policy_requires_review")
        basis.append(r["missing_data"]["id"])
    elif usd >= float(r["amount_review"]["threshold_usd"]):
        reasons.append("amount_above_threshold")
        basis.append(r["amount_review"]["id"])
    fraud = r["fraud_escalation"]
    score = facts.get("fraud_score")
    if (score is not None and float(score) >= float(fraud["fraud_score_at_least"])) or bool(facts.get("is_fraud")):
        reasons.append("suspected_fraud")
        basis.append(fraud["id"])
    if reasons:
        top = min(reasons, key=PRIORITY.index)
        return Gold("handoff", top, inside, tuple(basis), usd, deadline.isoformat())
    return Gold("resolved", None, True, tuple(basis), usd, deadline.isoformat())
