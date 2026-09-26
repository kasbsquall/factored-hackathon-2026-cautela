"""SYN-FX-001: fixed official rates let the USD 450 threshold apply to local-currency charges with no amount_usd."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.orchestrator.actions import days_since, usd_fact
from agent.policy.engine import PolicyInput, TransactionFacts, evaluate, load_rules, usd_amount

AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
RULES = load_rules()
RATES = RULES["fx_rates"]["units_per_usd"]


def tx(**overrides) -> TransactionFacts:
    base = {"transaction_id": "TX1", "transaction_date": (AS_OF - timedelta(days=3)).replace(tzinfo=None),
            "amount": 100.0, "currency": "USD", "amount_usd": None, "channel": "POS",
            "transaction_status": "Approved", "fraud_score": 1.0, "is_fraud": False, "product_type": "Debit Card"}
    return TransactionFacts(**{**base, **overrides})


def decide(country: str, transaction: TransactionFacts):
    return evaluate(PolicyInput(customer_country=country, as_of=AS_OF, transaction=transaction))


def test_the_rule_is_labeled_dated_and_sourced():
    rule = RULES["fx_rates"]
    assert rule["id"] == "SYN-FX-001" and rule["source"] == "synthetic_policy"
    assert rule["verification"] == "verified_primary" and rule["reference_date"] == "2026-09-25"
    assert set(RATES) == {"MXN", "COP", "ARS", "BRL"}
    for entry in RATES.values():
        assert entry["rate"] > 0 and entry["url"].startswith("https://") and entry["publisher"]


@pytest.mark.parametrize("currency", ["MXN", "COP", "ARS", "BRL"])
def test_a_listed_currency_without_amount_usd_is_converted_at_its_fixed_rate(currency):
    amount = 100 * RATES[currency]["rate"]  # exactly USD 100
    d = decide("Mexico", tx(amount=amount, currency=currency))
    assert "SYN-FX-001" in d.rule_ids and "SYN-DATA-001" not in d.rule_ids
    assert d.facts["amount_usd"] == pytest.approx(100.0)
    assert d.facts["amount_usd_fx"] == {"rule_id": "SYN-FX-001", "currency": currency,
                                        "rate": RATES[currency]["rate"], "reference_date": "2026-09-25"}
    assert not d.must_escalate and d.allows("open_dispute_case")
    hit = next(h for h in d.rules_fired if h.rule_id == "SYN-FX-001")
    assert hit.source == "synthetic_policy" and hit.verification == "verified_primary"
    assert RATES[currency]["publisher"] in hit.message


@pytest.mark.parametrize(("usd", "escalates"), [(449.99, False), (450.0, True), (1200.0, True)])
def test_the_converted_amount_goes_through_the_usd_450_threshold(usd, escalates):
    rate = RATES["COP"]["rate"]
    d = decide("Colombia", tx(amount=round(usd * rate, 2), currency="COP", channel="POS"))
    assert ("amount_above_threshold" in d.escalation_reasons) is escalates
    assert ("SYN-AMOUNT-001" in d.rule_ids) is escalates and "SYN-FX-001" in d.rule_ids


def test_amount_usd_from_the_data_wins_over_the_table():
    d = decide("Argentina", tx(amount=150000, currency="ARS", amount_usd=428.57))
    assert "SYN-FX-001" not in d.rule_ids and d.facts["amount_usd"] == 428.57
    assert "amount_usd_fx" not in d.facts


def test_a_usd_charge_needs_no_conversion():
    assert usd_amount(tx(amount=80.0, currency="USD"), RULES) == (80.0, None)


@pytest.mark.parametrize("currency", ["EUR", "CLP", None])
def test_an_unknown_currency_keeps_syn_data_001(currency):
    d = decide("Mexico", tx(amount=1000.0, currency=currency))
    assert "SYN-DATA-001" in d.rule_ids and "SYN-FX-001" not in d.rule_ids
    assert d.primary_reason == "policy_requires_review"


def test_the_handoff_fact_says_the_usd_amount_was_converted():
    d = decide("Mexico", tx(amount=1771.0, currency="MXN"))
    assert usd_fact(d.facts) == ("USD amount 100.00 (not in the data; converted from MXN at the fixed synthetic rate "
                                 "17.71 per USD of 2026-09-25, SYN-FX-001)")
    assert usd_fact({"amount_usd": 12.5}) == "USD amount 12.50"
    assert usd_fact({"amount_usd": None}) == "USD amount unknown"


@pytest.mark.parametrize(("days", "text"), [(0, "0 days since the charge"), (1, "1 day since the charge"),
                                            (2, "2 days since the charge")])
def test_days_since_the_charge_is_pluralized(days, text):
    assert days_since(days) == text
