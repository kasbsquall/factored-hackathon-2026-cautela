"""Policy engine: per-country windows, thresholds, fraud escalation, scope, and narrowing only."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.policy.engine import (
    READ_ACTIONS,
    REASON_PRIORITY,
    WRITE_ACTIONS,
    ModelProposal,
    PolicyInput,
    ProductFacts,
    TransactionFacts,
    add_business_days,
    evaluate,
    load_rules,
    narrow,
)

AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)  # a Monday


def tx(days_ago: int = 3, **overrides) -> TransactionFacts:
    base = {"transaction_id": "TX1", "transaction_date": (AS_OF - timedelta(days=days_ago)).replace(tzinfo=None),
            "amount": 100.0, "currency": "USD", "amount_usd": None, "channel": "POS",
            "transaction_status": "Approved", "fraud_score": 10.0, "is_fraud": False, "product_type": "Debit Card"}
    return TransactionFacts(**{**base, **overrides})


def decide(country: str = "Mexico", transaction: TransactionFacts | None = None, **kwargs):
    return evaluate(PolicyInput(customer_country=country, as_of=kwargs.pop("as_of", AS_OF),
                                transaction=transaction, **kwargs))


def test_normal_case_allows_dispute_with_confirmation_and_no_escalation():
    d = decide("Mexico", tx(10))
    assert d.allows("open_dispute_case") and not d.must_escalate
    assert d.required_confirmations == ["open_dispute_case"]
    assert {"MX-WINDOW-001", "SYN-CONFIRM-001"} <= set(d.rule_ids)
    assert set(READ_ACTIONS) <= set(d.allowed_actions)


@pytest.mark.parametrize(("country", "days", "inside", "rule"), [
    ("Mexico", 90, True, "MX-WINDOW-001"),
    ("Mexico", 91, False, "MX-WINDOW-001"),
    ("México", 10, True, "MX-WINDOW-001"),
    ("Colombia", 30, True, "CO-WINDOW-002"),
    ("Colombia", 91, False, "CO-WINDOW-002"),
    ("Argentina", 30, True, "AR-WINDOW-002"),
    ("Argentina", 31, False, "AR-WINDOW-002"),
])
def test_claim_window_per_country(country, days, inside, rule):
    d = decide(country, tx(days))
    assert rule in d.rule_ids
    assert d.allows("open_dispute_case") is inside
    assert ("policy_requires_review" in d.escalation_reasons) is (not inside)


@pytest.mark.parametrize(("tx_day", "inside"), [
    (datetime(2026, 5, 25, 10), True),   # Monday; 5 business days end Monday 1 June, the as_of date
    (datetime(2026, 5, 22, 10), False),  # Friday; deadline Friday 29 May
])
def test_colombia_remote_purchase_uses_decree_587_business_days(tx_day, inside):
    d = decide("Colombia", tx(transaction_date=tx_day, channel="Web"))
    assert "CO-WINDOW-001" in d.rule_ids
    assert d.allows("open_dispute_case") is inside


def test_argentina_credit_card_uses_ley_25065():
    inside, outside = decide("Argentina", tx(30, product_type="Credit Card")), \
        decide("Argentina", tx(31, product_type="Credit Card"))
    assert "AR-WINDOW-001" in inside.rule_ids and inside.allows("open_dispute_case")
    assert not outside.allows("open_dispute_case") and outside.primary_reason == "policy_requires_review"
    hit = next(h for h in inside.rules_fired if h.rule_id == "AR-WINDOW-001")
    assert hit.source == "legal" and hit.verification == "verified_primary"


def test_business_days_skip_weekends():
    assert add_business_days(datetime(2026, 5, 29).date(), 1) == datetime(2026, 6, 1).date()


@pytest.mark.parametrize(("usd", "escalates"), [(449.99, False), (450.0, True), (5000.0, True)])
def test_amount_threshold_forces_review_but_keeps_intake(usd, escalates):
    d = decide("Mexico", tx(amount=usd, currency="USD"))
    assert ("amount_above_threshold" in d.escalation_reasons) is escalates
    assert d.allows("open_dispute_case")  # the case is registered; a human reviews it


def test_threshold_uses_amount_usd_for_local_currency():
    d = decide("Colombia", tx(amount=2_500_000, currency="COP", amount_usd=610.0))
    assert "amount_above_threshold" in d.escalation_reasons


def test_missing_usd_amount_in_an_unlisted_currency_goes_to_review():
    d = decide("Argentina", tx(amount=150000, currency="EUR", amount_usd=None))  # EUR is not in SYN-FX-001
    assert "SYN-DATA-001" in d.rule_ids and "policy_requires_review" in d.escalation_reasons


@pytest.mark.parametrize(("score", "flag", "escalates"), [(49.99, False, False), (50.0, False, True),
                                                          (5.0, True, True), (None, False, False)])
def test_fraud_signal_escalates(score, flag, escalates):
    d = decide("Mexico", tx(fraud_score=score, is_fraud=flag))
    assert ("suspected_fraud" in d.escalation_reasons) is escalates
    assert ("SYN-FRAUD-001" in d.rule_ids) is escalates


def test_block_card_only_for_own_active_card():
    active = decide("Mexico", tx(), product=ProductFacts(product_id="P1", product_type="Debit Card",
                                                          product_status="Active"))
    blocked = decide("Mexico", tx(), product=ProductFacts(product_id="P1", product_type="Debit Card",
                                                           product_status="Blocked"))
    loan = decide("Mexico", tx(), product=ProductFacts(product_id="P2", product_type="Personal Loan",
                                                        product_status="Active"))
    assert active.allows("block_card") and "block_card" in active.required_confirmations
    assert not blocked.allows("block_card") and not loan.allows("block_card")


@pytest.mark.parametrize(("status", "rule"), [("Declined", "SYN-STATUS-002"), ("Reversed", "SYN-STATUS-003")])
def test_not_disputable_statuses_explain_without_action(status, rule):
    d = decide("Mexico", tx(transaction_status=status))
    assert rule in d.rule_ids and not d.allows("open_dispute_case") and not d.must_escalate


@pytest.mark.parametrize("problem", [{"amount": None}, {"transaction_status": "Weird"}, {"days_ago": -2}])
def test_bad_or_missing_data_goes_to_review(problem):
    days = problem.pop("days_ago", 3)
    d = decide("Mexico", tx(days, **problem))
    assert not d.allows("open_dispute_case") and d.primary_reason == "policy_requires_review"


def test_unknown_country_goes_to_review():
    d = decide("Brasil", tx())
    assert "SYN-DATA-001" in d.rule_ids and not d.allows("open_dispute_case")


@pytest.mark.parametrize("intent", ["loan_application", "money_transfer", "immediate_refund",
                                    "other_customer_request"])
def test_out_of_scope_intents_transfer_and_allow_no_writes(intent):
    d = decide("Mexico", tx(), intent=intent)
    assert d.primary_reason == "out_of_scope" and not set(WRITE_ACTIONS) & set(d.allowed_actions)


def test_customer_asking_for_a_human_is_transferred():
    assert decide("Mexico", tx(), customer_requested_human=True).primary_reason == "customer_requested_human"


def test_security_flags_remove_all_writes():
    d = decide("Mexico", tx(), security_flags=["cross_customer_access"],
               product=ProductFacts(product_id="P1", product_type="Credit Card", product_status="Active"))
    assert d.primary_reason == "security_event" and not set(WRITE_ACTIONS) & set(d.allowed_actions)


# ---- the model can only narrow --------------------------------------------------------------------------
def test_model_cannot_add_actions_skip_confirmation_or_cancel_escalation():
    base = decide("Mexico", tx(amount=900.0))  # escalates on amount, allows open_dispute_case
    result = narrow(base, ModelProposal(actions=["open_dispute_case", "block_card", "refund"], escalate=False,
                                        skip_confirmations=["open_dispute_case"], reason_codes=["approve_now"]))
    d = result.decision
    assert "block_card" not in d.allowed_actions and "refund" not in d.allowed_actions
    assert d.required_confirmations == ["open_dispute_case"]
    assert d.must_escalate and "amount_above_threshold" in d.escalation_reasons
    assert set(result.rejected) == {"add_action:block_card", "add_action:refund",
                                    "skip_confirmation:open_dispute_case", "unknown_reason:approve_now"}


def test_model_can_drop_actions_and_add_escalation():
    base = decide("Mexico", tx())
    d = narrow(base, ModelProposal(actions=["get_transaction"], escalate=True)).decision
    assert d.allowed_actions == ["get_transaction"] and d.escalation_reasons == ["low_confidence"]


def test_narrowing_never_widens_on_random_proposals():
    rng = random.Random(7)
    universe = list(READ_ACTIONS + WRITE_ACTIONS) + ["refund", "transfer_money"]
    bases = [decide("Mexico", tx()), decide("Mexico", tx(amount=1000.0)), decide("Mexico", tx(200)),
             decide("Argentina", tx(fraud_score=90.0))]
    for _ in range(300):
        base = rng.choice(bases)
        proposal = ModelProposal(actions=rng.sample(universe, rng.randint(0, len(universe))),
                                 escalate=rng.random() < 0.5, skip_confirmations=rng.sample(list(WRITE_ACTIONS), 1),
                                 reason_codes=rng.sample(list(REASON_PRIORITY) + ["nope"], rng.randint(0, 2)))
        d = narrow(base, proposal).decision
        assert set(d.allowed_actions) <= set(base.allowed_actions)
        assert set(base.escalation_reasons) <= set(d.escalation_reasons)
        assert set(base.required_confirmations) & set(d.allowed_actions) <= set(d.required_confirmations)
        assert d.must_escalate or not base.must_escalate


# ---- the rule file itself ------------------------------------------------------------------------------
def test_rule_file_is_well_formed_and_labeled():
    rules = load_rules()
    ids = [r["id"] for r in rules["claim_windows"] + rules["disputable_status"]] + [
        rules[k]["id"] for k in ("amount_review", "missing_data", "fraud_escalation", "confirmations",
                                 "out_of_scope", "human_request", "security_event")]
    assert len(ids) == len(set(ids))
    for rule in rules["claim_windows"]:
        assert rule["source"] in {"legal", "synthetic_policy"} and rule["citation"]
        if rule["source"] == "legal":
            assert rule["url"].startswith("http")
        else:
            assert rule["verification"] == "pending_verification"
    assert set(WRITE_ACTIONS) <= set(rules["confirmations"]["actions"])


def test_reason_codes_match_handoff_schema():
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs/schemas/handoff.schema.json").read_text())
    assert set(REASON_PRIORITY) == set(schema["properties"]["transfer_reason"]["properties"]["code"]["enum"])
