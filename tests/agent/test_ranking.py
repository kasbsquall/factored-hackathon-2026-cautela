"""Rule-based candidate ranker, the ambiguity rule, and a check against the fixture's dispute ground truth.

The fixture ties each dispute complaint to the transaction it refers to (manifest.json, dispute_links). That is
team-generated ground truth for a templated text, so the numbers below are a sanity check of the linking logic,
not an estimate of accuracy on real customers.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

from agent.tools.ranking import (
    AMBIGUITY_MIN_MARGIN,
    AMBIGUITY_MIN_TOP,
    CandidateRanker,
    DescriptionFeatures,
    RuleBasedRanker,
    is_ambiguous,
)

TEXT = re.compile(r"(?P<amount>\d+\.\d{2}) (?P<cur>[A-Z]{3}) en (?P<merchant>.+?) del (?P<date>\d{2}/\d{2}/\d{4})")


def tx(tid: str, amount: float, day: date, merchant: str | None = "Farmacia San Rafael", currency: str = "MXN"):
    return {"transaction_id": tid, "amount": amount, "currency": currency, "merchant_name": merchant,
            "transaction_date": datetime.combine(day, datetime.min.time())}


def test_default_ranker_satisfies_protocol():
    assert isinstance(RuleBasedRanker(), CandidateRanker)


def test_exact_hints_rank_the_right_charge_first():
    rows = [tx("A", 120.0, date(2026, 5, 1)), tx("B", 980.0, date(2026, 5, 3), "Cine Plaza Mayor"),
            tx("C", 121.0, date(2026, 4, 1))]
    ranked = RuleBasedRanker().rank(DescriptionFeatures(amount=120.0, currency="MXN", date_hint=date(2026, 5, 1),
                                                        merchant_hint="farmacia"), rows)
    assert ranked[0][0] == "A" and not is_ambiguous([s for _, s in ranked])


def test_currency_mismatch_zeroes_the_amount_component():
    ranker = RuleBasedRanker()
    features = DescriptionFeatures(amount=120.0, currency="USD")
    assert ranker.score(features, tx("A", 120.0, date(2026, 5, 1))) == 0.0


def test_two_equal_charges_are_ambiguous():
    rows = [tx("A", 120.0, date(2026, 5, 1)), tx("B", 120.0, date(2026, 5, 2))]
    ranked = RuleBasedRanker().rank(DescriptionFeatures(amount=120.0), rows)
    assert is_ambiguous([s for _, s in ranked])


@pytest.mark.parametrize(("scores", "expected"), [
    ([], True), ([AMBIGUITY_MIN_TOP - 0.01], True), ([0.9], False),
    ([0.9, 0.9 - AMBIGUITY_MIN_MARGIN + 0.01], True), ([0.9, 0.7], False),
])
def test_ambiguity_rule(scores, expected):
    assert is_ambiguous(scores) is expected


def _ground_truth_eval(query, manifest, use_full_hints: bool) -> tuple[int, int, int, int]:
    links = manifest["dispute_links"]
    complaints = {r["complaint_id"]: r for r in query(
        "SELECT complaint_id, customer_id, description, claimed_amount, currency, creation_date "
        "FROM silver.complaints WHERE claimed_amount IS NOT NULL")}
    ranker, n, top1, top3, ambiguous_wrong = RuleBasedRanker(), 0, 0, 0, 0
    for complaint_id, true_tx in links.items():
        c = complaints.get(complaint_id)
        match = TEXT.search(c["description"]) if c else None
        if match is None:
            continue
        rows = query("SELECT transaction_id, amount, currency, merchant_name, transaction_date "
                     "FROM silver.transactions WHERE customer_id = ? AND transaction_date BETWEEN ? AND ? "
                     "AND transaction_type <> 'Deposit'",
                     [c["customer_id"], c["creation_date"] - timedelta(days=90), c["creation_date"]])
        if use_full_hints:
            features = DescriptionFeatures(amount=float(match["amount"]), currency=match["cur"],
                                           date_hint=datetime.strptime(match["date"], "%d/%m/%Y").date(),
                                           merchant_hint=match["merchant"])
        else:  # the customer only remembers the amount and says "about a week ago"
            features = DescriptionFeatures(amount=float(match["amount"]),
                                           date_hint=c["creation_date"].date() - timedelta(days=7),
                                           date_tolerance_days=7)
        ranked = ranker.rank(features, [{**r, "amount": float(r["amount"])} for r in rows])
        ids = [t for t, _ in ranked]
        n += 1
        top1 += ids[:1] == [true_tx]
        top3 += true_tx in ids[:3]
        ambiguous_wrong += ids[:1] != [true_tx] and not is_ambiguous([s for _, s in ranked])
    return n, top1, top3, ambiguous_wrong


def test_ranker_recovers_fixture_dispute_links_with_full_hints(query, fixture_source):
    n, top1, top3, confident_wrong = _ground_truth_eval(query, fixture_source[1], use_full_hints=True)
    assert n >= 100
    assert top1 / n >= 0.95 and top3 / n >= 0.98  # seed 42: 169 of 169
    assert confident_wrong == 0  # when it is wrong it must say it is unsure


def test_ranker_with_amount_only_never_guesses_wrong_confidently(query, fixture_source):
    n, top1, _, confident_wrong = _ground_truth_eval(query, fixture_source[1], use_full_hints=False)
    assert n >= 100 and top1 / n >= 0.9  # seed 42: 162 of 169
    assert confident_wrong == 0  # the misses are all flagged ambiguous
