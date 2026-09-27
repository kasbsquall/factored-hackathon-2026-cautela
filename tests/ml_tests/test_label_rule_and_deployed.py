"""The label-rule baseline (ml/label_rule.py) and the deployed abstain rule (ml.decision.deployed_abstain_rule)."""

from __future__ import annotations

import pytest

from ml.decision import deployed_abstain_rule
from ml.disposition import DeployedDecider
from ml.label_rule import LabelRuleDecider, rule_fits

INP = {"text": "me cobraron 250 dólares ayer en una compra", "report_date": "2026-03-10", "language": "es"}


def tx(tid: str, amount: float, day: str, status: str = "Approved", kind: str = "Purchase") -> dict:
    return {"transaction_id": tid, "amount": amount, "currency": "USD", "transaction_date": f"{day} 12:00:00",
            "transaction_type": kind, "transaction_status": status, "channel": "POS", "merchant_name": None,
            "merchant_category": None, "transaction_city": "Guadalajara", "transaction_country": "Mexico"}


def ranked(pool: list[dict]) -> list[tuple[str, float]]:
    return [(c["transaction_id"], 0.9 - 0.1 * i) for i, c in enumerate(pool)]


def test_one_consistent_charge_is_acted_on():
    pool = [tx("B", 900.0, "2026-02-01"), tx("A", 251.0, "2026-03-09")]
    conf, d = LabelRuleDecider().decide_case(INP, pool, ranked(pool))
    assert d == {"decision": "act", "top_k": ["A"]} and conf == 1.0


def test_declined_charge_is_not_consistent_as_in_the_labels():
    pool = [tx("C", 250.0, "2026-03-09", status="Declined"), tx("B", 900.0, "2026-02-01")]
    _, full, _ = rule_fits(INP, pool)
    assert full == []
    assert LabelRuleDecider().decide_case(INP, pool, ranked(pool))[1]["decision"] == "abstain"


def test_two_consistent_charges_are_offered_in_ranker_order():
    pool = [tx("A", 249.0, "2026-03-09"), tx("B", 900.0, "2026-02-01"), tx("A2", 252.0, "2026-03-09")]
    order = [("A2", 0.9), ("B", 0.5), ("A", 0.4)]
    assert LabelRuleDecider().decide_case(INP, pool, order)[1] == {"decision": "clarify", "top_k": ["A2", "A"]}


def test_no_cue_read_means_abstain():
    inp = dict(INP, text="hola")
    pool = [tx("A", 250.0, "2026-03-09")]
    assert rule_fits(inp, pool)[0] == 0
    assert LabelRuleDecider().decide_case(inp, pool, ranked(pool))[1]["decision"] == "abstain"


@pytest.mark.parametrize("p, want", [
    ({"match": 0.88, "ambiguous": 0.01, "no_match": 0.11}, ("clarify", "abstain_to_clarify")),
    ({"match": 0.15, "ambiguous": 0.01, "no_match": 0.84}, ("abstain", None)),
])
def test_deployed_rule_keeps_an_abstention_only_when_no_match_is_most_likely(p, want):
    rk = [("T0", 0.9), ("T1", 0.8), ("T2", 0.7), ("T3", 0.6)]
    got, note = deployed_abstain_rule({"decision": "abstain", "top_k": []}, rk, p)
    assert (got["decision"], note) == want
    if want[0] == "clarify":
        assert got["top_k"] == ["T0", "T1", "T2"]


def test_deployed_rule_never_changes_an_act_or_a_clarify():
    rk = [("T0", 0.9)]
    for d in ({"decision": "act", "top_k": ["T0"]}, {"decision": "clarify", "top_k": ["T0"]}):
        assert deployed_abstain_rule(d, rk, {"match": 0.1, "ambiguous": 0.1, "no_match": 0.8}) == (d, None)


class _Inner:
    name, policy = "stub", type("P", (), {"k": 3})()

    def decide_case(self, inp, candidates, ranked_):
        return 0.7, {"decision": "abstain", "top_k": []}

    def proba(self, inp, candidates, ranked_):
        return {"match": 0.7, "ambiguous": 0.2, "no_match": 0.1}


def test_deployed_decider_scores_what_the_service_decides():
    rk = [("T0", 0.9), ("T1", 0.8)]
    assert DeployedDecider(_Inner()).decide_case({}, [], rk) == (0.7, {"decision": "clarify", "top_k": ["T0", "T1"]})
