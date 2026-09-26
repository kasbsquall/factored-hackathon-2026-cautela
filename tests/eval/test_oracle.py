"""The independent policy oracle (eval/oracle.py) that sets gold outcomes, and its agreement with rules.yaml."""

from __future__ import annotations

from datetime import datetime

from eval.oracle import gold_for_charge, rules

NOW = datetime(2026, 3, 16, 12)  # a Monday


def charge(**kw) -> dict:
    base = {"customer_country": "Mexico", "transaction_date": datetime(2026, 3, 10, 9), "amount": 100.0,
            "currency": "USD", "amount_usd": 100.0, "transaction_status": "Approved", "product_type": "Credit Card",
            "channel": "POS", "fraud_score": 1.0, "is_fraud": False}
    return base | kw


def test_inside_window_under_threshold_resolves_with_a_case():
    g = gold_for_charge(charge(), NOW)
    assert (g.kind, g.reason, g.expects_case) == ("resolved", None, True)
    assert g.rule_basis == ("MX-WINDOW-001",)


def test_threshold_is_inclusive_and_writes_a_case_for_review():
    threshold = float(rules()["amount_review"]["threshold_usd"])
    g = gold_for_charge(charge(amount=threshold, amount_usd=threshold), NOW)
    assert (g.kind, g.reason, g.expects_case) == ("handoff", "amount_above_threshold", True)
    assert gold_for_charge(charge(amount_usd=threshold - 0.01), NOW).kind == "resolved"


def test_fraud_outranks_amount():
    g = gold_for_charge(charge(amount_usd=900.0, fraud_score=50.0), NOW)
    assert g.reason == "suspected_fraud" and set(g.rule_basis) >= {"SYN-FRAUD-001", "SYN-AMOUNT-001"}
    assert gold_for_charge(charge(is_fraud=True), NOW).reason == "suspected_fraud"


def test_outside_window_transfers_without_a_write():
    g = gold_for_charge(charge(transaction_date=datetime(2025, 12, 1)), NOW)
    assert (g.kind, g.reason, g.expects_case) == ("handoff", "policy_requires_review", False)


def test_colombia_web_counts_business_days():
    # Wednesday 2026-03-11 + 5 business days = Wednesday 2026-03-18
    tx = charge(customer_country="Colombia", channel="Web", currency="COP", amount=100000.0, amount_usd=25.0,
                transaction_date=datetime(2026, 3, 11, 10))
    assert gold_for_charge(tx, datetime(2026, 3, 18, 12)).kind == "resolved"
    assert gold_for_charge(tx, datetime(2026, 3, 19, 12)).reason == "policy_requires_review"
    assert gold_for_charge(tx | {"channel": "POS"}, datetime(2026, 3, 19, 12)).kind == "resolved"  # CO-WINDOW-002


def test_argentina_credit_card_window_is_30_days():
    tx = charge(customer_country="Argentina", transaction_date=datetime(2026, 2, 1))
    assert gold_for_charge(tx, datetime(2026, 3, 3, 12)).kind == "resolved"
    assert gold_for_charge(tx, datetime(2026, 3, 4, 12)).kind == "handoff"


def test_missing_usd_is_converted_at_the_fixed_rate_or_reviewed():
    rate = float(rules()["fx_rates"]["units_per_usd"]["COP"]["rate"])
    g = gold_for_charge(charge(customer_country="Colombia", currency="COP", amount=rate * 500, amount_usd=None), NOW)
    assert g.reason == "amount_above_threshold" and g.usd == 500.0
    g = gold_for_charge(charge(currency="EUR", amount=10.0, amount_usd=None), NOW)
    assert (g.kind, g.reason, g.expects_case) == ("handoff", "policy_requires_review", True)


def test_declined_and_reversed_are_not_disputable():
    assert gold_for_charge(charge(transaction_status="Declined"), NOW).kind == "not_disputable"
    assert gold_for_charge(charge(transaction_status="Reversed"), NOW).kind == "not_disputable"


def test_missing_fields_or_future_dates_go_to_review_without_a_write():
    g = gold_for_charge(charge(amount=None), NOW)
    assert (g.reason, g.expects_case) == ("policy_requires_review", False)
    assert gold_for_charge(charge(transaction_date=datetime(2026, 3, 20)), NOW).reason == "policy_requires_review"


def test_accented_country_names_are_normalized():
    assert gold_for_charge(charge(customer_country="México"), NOW).kind == "resolved"
