"""Deterministic policy engine for unrecognized-charge intake.

evaluate() turns verified facts (never model prose) into a PolicyDecision: the actions allowed, the ones that
need an explicit customer confirmation, whether the case must go to a human and why (reason codes from
docs/schemas/handoff.schema.json), and the ids of every rule that fired, each with its source.

narrow() is the only way a model proposal touches a decision. It can drop actions and add escalation reasons.
It cannot add an action, remove a required confirmation or cancel an escalation; attempts are reported.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

RULES_PATH = Path(__file__).with_name("rules.yaml")

READ_ACTIONS = ("find_candidate_charges", "get_case_status", "get_customer_profile", "get_dispute_policy",
                "get_transaction", "list_recent_transactions")
WRITE_ACTIONS = ("block_card", "open_dispute_case")
CARD_PRODUCTS = frozenset({"Credit Card", "Debit Card"})
REASON_PRIORITY = ("security_event", "suspected_fraud", "customer_requested_human", "out_of_scope",
                   "amount_above_threshold", "policy_requires_review", "low_confidence", "tool_failure")
COUNTRIES = {"mexico": "Mexico", "colombia": "Colombia", "argentina": "Argentina"}


class TransactionFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    transaction_id: str
    transaction_date: datetime | None
    amount: float | None
    currency: str | None
    amount_usd: float | None = None
    channel: str | None = None
    transaction_status: str | None
    fraud_score: float | None = None
    is_fraud: bool | None = None
    product_type: str | None = None


class ProductFacts(BaseModel):
    product_id: str
    product_type: str | None
    product_status: str | None


class PolicyInput(BaseModel):
    intent: str = "unrecognized_charge"
    customer_country: str | None
    as_of: datetime
    transaction: TransactionFacts | None = None
    product: ProductFacts | None = None
    customer_requested_human: bool = False
    security_flags: list[str] = Field(default_factory=list)


class RuleHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    source: str
    verification: str | None = None
    message: str


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str
    allowed_actions: list[str]
    required_confirmations: list[str]
    must_escalate: bool
    escalation_reasons: list[str]
    rules_fired: list[RuleHit]
    facts: dict[str, Any] = Field(default_factory=dict)

    @property
    def rule_ids(self) -> list[str]:
        return [hit.rule_id for hit in self.rules_fired]

    @property
    def primary_reason(self) -> str | None:
        return next((r for r in REASON_PRIORITY if r in self.escalation_reasons), None)

    def allows(self, action: str) -> bool:
        return action in self.allowed_actions


class ModelProposal(BaseModel):
    """What a model may suggest: a subset of actions and extra escalation. Anything else is ignored."""

    actions: list[str] = Field(default_factory=list)
    escalate: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    skip_confirmations: list[str] = Field(default_factory=list)


class NarrowResult(BaseModel):
    decision: PolicyDecision
    rejected: list[str]


@lru_cache(maxsize=4)
def load_rules(path: str = str(RULES_PATH)) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    plain = "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")
    return COUNTRIES.get(plain.strip().casefold())


def add_business_days(start: date, days: int) -> date:
    """Weekdays only. Public holidays are not modeled (documented in rules.yaml)."""
    current, added = start, 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _matches(value: str | None, allowed: Any) -> bool:
    return allowed == "any" or (value is not None and value in allowed)


def select_window(rules: dict, country: str, product_type: str | None, channel: str | None) -> dict | None:
    """First matching window in file order; specific rules are listed before generic ones."""
    for rule in rules["claim_windows"]:
        if rule["country"] == country and _matches(product_type, rule["products"]) \
                and _matches(channel, rule["channels"]):
            return rule
    return None


def window_deadline(rule: dict, start: date) -> date:
    if rule["calendar"] == "business":
        return add_business_days(start, rule["days"])
    return start + timedelta(days=rule["days"])


class _Builder:
    def __init__(self, rules: dict) -> None:
        self.rules = rules
        self.writes: set[str] = set()
        self.reasons: list[str] = []
        self.hits: list[RuleHit] = []
        self.facts: dict[str, Any] = {}

    def fire(self, rule: dict, message: str, reason: str | None = None) -> None:
        self.hits.append(RuleHit(rule_id=rule["id"], source=rule["source"],
                                 verification=rule.get("verification"), message=message))
        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    def build(self) -> PolicyDecision:
        confirm = set(self.rules["confirmations"]["actions"])
        needs = sorted(self.writes & confirm)
        if needs:
            self.fire(self.rules["confirmations"], f"confirmation required for {', '.join(needs)}")
        return PolicyDecision(
            policy_version=self.rules["version"], allowed_actions=sorted(set(READ_ACTIONS) | self.writes),
            required_confirmations=needs, must_escalate=bool(self.reasons), escalation_reasons=self.reasons,
            rules_fired=self.hits, facts=self.facts,
        )


def usd_amount(tx: TransactionFacts, rules: dict) -> tuple[float | None, dict | None]:
    """(USD amount, the SYN-FX-001 rate entry when the amount was converted). The data's amount_usd wins; a USD
    charge is its own amount; a listed currency is converted at its fixed rate; anything else is unknown."""
    if tx.amount_usd is not None:
        return tx.amount_usd, None
    if tx.currency == "USD":
        return tx.amount, None
    entry = rules.get("fx_rates", {}).get("units_per_usd", {}).get(tx.currency or "")
    if entry is None or tx.amount is None:
        return None, None
    return round(tx.amount / float(entry["rate"]), 2), entry


def _evaluate_transaction(b: _Builder, tx: TransactionFacts, country: str | None, as_of: datetime) -> None:
    rules = b.rules
    missing = [f for f in ("transaction_date", "amount", "currency", "transaction_status") if getattr(tx, f) is None]
    if missing or country is None or tx.transaction_date.date() > as_of.date():
        why = f"missing {', '.join(missing)}" if missing else "unknown country" if country is None \
            else "transaction dated after today"
        b.fire(rules["missing_data"], f"cannot evaluate the charge: {why}", "policy_requires_review")
        return
    status_rule = next((r for r in rules["disputable_status"] if tx.transaction_status in r["statuses"]), None)
    if status_rule is None:
        b.fire(rules["missing_data"], f"unknown status {tx.transaction_status}", "policy_requires_review")
        return
    if status_rule["id"] != "SYN-STATUS-001":
        b.fire(status_rule, f"status {tx.transaction_status} is not disputable")
        return
    window = select_window(rules, country, tx.product_type, tx.channel)
    if window is None:
        b.fire(rules["missing_data"], f"no claim window for {country}", "policy_requires_review")
        return
    # Known limitation (data_engineering/README.md, "Event time and the claim window"): the window counts from the
    # stored calendar date. In the organizer data every stored timestamp falls between 06:00 of its partition date
    # and 06:00 of the next day, in all three countries, so a charge stored before 06:00 sits in the previous day's
    # partition. Counting from the partition date would start the window one day earlier in 262 of the 1,017 held-out
    # conversations that target a charge, and flip 3 of them from inside to outside a 30-day window. eval/oracle.py
    # uses the stored date too, so a fix changes the engine and the oracle together and re-freezes the suites.
    start = tx.transaction_date.date()
    deadline = window_deadline(window, start)
    b.facts.update(window_rule=window["id"], window_deadline=deadline.isoformat(),
                   days_since_transaction=(as_of.date() - start).days)
    inside = as_of.date() <= deadline
    b.fire(window, f"claim window {window['days']} {window['calendar']} days, deadline {deadline.isoformat()}: "
                   f"{'inside' if inside else 'outside'}", None if inside else "policy_requires_review")
    if inside:
        b.writes.add("open_dispute_case")
    usd, fx = usd_amount(tx, rules)
    b.facts["amount_usd"] = usd
    if fx is not None:
        rule = rules["fx_rates"]
        b.facts["amount_usd_fx"] = {"rule_id": rule["id"], "currency": tx.currency, "rate": fx["rate"],
                                    "basis": rule["basis"]}
        b.fire(rule, f"no USD amount in the data: USD {usd:.2f} from {tx.amount:.2f} {tx.currency} at the fixed "
                     f"rate {fx['rate']} {tx.currency} per USD ({rule['basis']})")
    if usd is None:
        b.fire(rules["missing_data"], "USD amount unknown, threshold cannot be applied", "policy_requires_review")
    elif usd >= rules["amount_review"]["threshold_usd"]:
        b.fire(rules["amount_review"], f"USD {usd:.2f} at or above review threshold", "amount_above_threshold")
    fraud = rules["fraud_escalation"]
    if (tx.fraud_score is not None and tx.fraud_score >= fraud["fraud_score_at_least"]) or tx.is_fraud:
        b.fire(fraud, f"fraud signal (score {tx.fraud_score}, flag {tx.is_fraud})", "suspected_fraud")
        b.facts["fraud_signal"] = True


def evaluate(policy_input: PolicyInput, rules: dict | None = None) -> PolicyDecision:
    rules = rules or load_rules()
    b = _Builder(rules)
    country = normalize_country(policy_input.customer_country)
    if policy_input.security_flags:
        b.fire(rules["security_event"], f"security flags: {', '.join(sorted(policy_input.security_flags))}",
               "security_event")
        return b.build()
    if policy_input.intent in rules["out_of_scope"]["intents"]:
        b.fire(rules["out_of_scope"], f"intent {policy_input.intent} is out of scope", "out_of_scope")
        return b.build()
    if policy_input.customer_requested_human:
        b.fire(rules["human_request"], "customer asked for a person", "customer_requested_human")
    if policy_input.transaction is not None:
        _evaluate_transaction(b, policy_input.transaction, country, policy_input.as_of)
    product = policy_input.product
    if product and product.product_type in CARD_PRODUCTS and product.product_status == "Active":
        b.writes.add("block_card")
    return b.build()


def narrow(decision: PolicyDecision, proposal: ModelProposal) -> NarrowResult:
    """Apply a model proposal. The result is never more permissive than `decision`."""
    rejected = [f"add_action:{a}" for a in proposal.actions if a not in decision.allowed_actions]
    rejected += [f"skip_confirmation:{a}" for a in proposal.skip_confirmations]
    valid = set(REASON_PRIORITY)
    rejected += [f"unknown_reason:{r}" for r in proposal.reason_codes if r not in valid]
    added = [r for r in proposal.reason_codes if r in valid and r not in decision.escalation_reasons]
    if proposal.escalate and not added and not decision.escalation_reasons:
        added = ["low_confidence"]
    allowed = [a for a in decision.allowed_actions if a in set(proposal.actions)]
    reasons = decision.escalation_reasons + added
    narrowed = decision.model_copy(update={
        "allowed_actions": allowed,
        "required_confirmations": [a for a in decision.required_confirmations if a in allowed],
        "must_escalate": bool(reasons), "escalation_reasons": reasons,
    })
    return NarrowResult(decision=narrowed, rejected=rejected)
