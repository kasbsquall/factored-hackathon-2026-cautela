"""The claim window counts from the stored calendar date of the charge (a documented limitation: the organizer data
stores times up to six hours past the partition's business day). Changing that is a deliberate policy change that
must move eval/oracle.py with it, so these tests pin the convention at the boundary."""

from datetime import UTC, datetime

from agent.policy.engine import PolicyInput, TransactionFacts, evaluate


def _decide(stored: datetime, as_of: datetime, country: str = "Argentina", product: str = "Debit Card"):
    tx = TransactionFacts(transaction_id="TX1", transaction_date=stored, amount=50.0, currency="USD",
                          amount_usd=50.0, channel="POS", transaction_status="Approved", fraud_score=1.0,
                          is_fraud=False, product_type=product)
    return evaluate(PolicyInput(customer_country=country, as_of=as_of, transaction=tx))


def test_a_charge_stored_before_six_counts_from_its_stored_date():
    # Stored 2026-03-12 04:52, which in the delivery belongs to the 2026-03-11 partition (business day).
    d = _decide(datetime(2026, 3, 12, 4, 52), datetime(2026, 4, 11, 12, tzinfo=UTC))
    assert d.facts["window_rule"] == "AR-WINDOW-002"
    assert d.facts["window_deadline"] == "2026-04-11"  # 30 days from 03-12; from 03-11 it would be 04-10
    assert d.allows("open_dispute_case")


def test_the_day_after_the_deadline_is_outside_whatever_the_hour():
    d = _decide(datetime(2026, 3, 12, 23, 59), datetime(2026, 4, 12, 12, tzinfo=UTC))
    assert not d.allows("open_dispute_case") and "policy_requires_review" in d.escalation_reasons
