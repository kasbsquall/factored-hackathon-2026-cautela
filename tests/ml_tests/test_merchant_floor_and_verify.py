"""Merchant mismatch and generic-word tie-break, the business act floor, and case-file verification."""

from __future__ import annotations

import json

import pytest

from ml.decision import DEFAULT_ACT_FLOOR, DecisionPolicy, FixedRuleDecider, decide
from ml.features.pairwise import merchant_idf_similarity, stated_merchants
from ml.features.parse import norm
from ml.rankers.rules import RuleRanker
from ml.scenarios.build import BuildConfig, build, payloads, verify_against, write_outputs
from ml.train import act_floor_setting
from tests.ml_tests.synthetic import make_world


def _tx(tid: str, merchant: str, amount: float = 120.0) -> dict:
    return {"transaction_id": tid, "amount": amount, "currency": "USD", "channel": "App", "merchant_category": "x",
            "merchant_name": merchant, "transaction_city": "Bogotá", "transaction_country": "Colombia",
            "transaction_date": "2026-03-10 10:00:00", "transaction_status": "Approved",
            "transaction_type": "Purchase"}


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(norm(text).split())


def test_a_clearly_different_stated_merchant_is_penalized():
    feats = {"text": "me cobraron 120 dólares de Uber el 10", "report_date": "2026-03-18"}
    ranked = dict(RuleRanker().rank(feats, [_tx("uber", "Uber"), _tx("taxi", "Taxi Seguro")]))
    assert ranked["uber"] > ranked["taxi"]
    assert stated_merchants(_tokens("un cobro de uber")) == frozenset({"Uber"})


def test_generic_word_does_not_tie_two_merchants():
    text = _tokens("un cargo en el laboratorio central")
    assert merchant_idf_similarity(text, "Laboratorio Central") > merchant_idf_similarity(text, "Mercado Central")
    feats = {"text": "un cargo de 120 dólares en el laboratorio central el 10", "report_date": "2026-03-18"}
    ranked = RuleRanker().rank(feats, [_tx("merc", "Mercado Central"), _tx("lab", "Laboratorio Central")])
    assert ranked[0][0] == "lab" and ranked[0][1] > ranked[1][1]


def test_act_floor_raises_the_effective_threshold_only_when_higher():
    ranked = [("a", 0.9), ("b", 0.1)]
    low = DecisionPolicy(t_act=0.30, t_abstain=0.99, act_floor=0.60)
    assert low.effective_t_act == 0.60
    assert decide(ranked, 0.50, 0.0, low)["decision"] == "clarify"
    assert decide(ranked, 0.65, 0.0, low)["decision"] == "act"
    high = DecisionPolicy(t_act=0.80, t_abstain=0.99, act_floor=0.60)
    assert high.effective_t_act == 0.80


def test_act_floor_setting_precedence(monkeypatch):
    monkeypatch.delenv("CAUTELA_ACT_FLOOR", raising=False)
    assert act_floor_setting(None) == DEFAULT_ACT_FLOOR
    monkeypatch.setenv("CAUTELA_ACT_FLOOR", "0.7")
    assert act_floor_setting(None) == 0.7
    assert act_floor_setting(0.5) == 0.5


def test_fixed_rule_ignores_the_floor():
    fixed = FixedRuleDecider()
    assert fixed.with_floor(0.9) is fixed


@pytest.fixture(scope="module")
def small_build():
    customers, txs = make_world()
    return build(customers, txs, BuildConfig(n_es={"train": 12, "val": 6, "test": 6},
                                             min_per_stratum={"train": 1, "val": 1, "test": 1}))


def test_verify_accepts_the_same_build_and_rejects_a_changed_one(small_build, tmp_path):
    cases, stats = small_build
    write_outputs(cases, stats, BuildConfig(), tmp_path, {})
    assert verify_against(tmp_path / "manifest.json", cases) == []
    changed = {**cases, "test": cases["test"][:-1]}
    problems = verify_against(tmp_path / "manifest.json", changed)
    assert any("test.jsonl" in p for p in problems) and any("data_version" in p for p in problems)


def test_verify_mode_never_rewrites_the_manifest(small_build, tmp_path):
    cases, stats = small_build
    write_outputs(cases, stats, BuildConfig(), tmp_path, {"note": "committed"})
    before = (tmp_path / "manifest.json").read_bytes()
    write_outputs(cases, stats, BuildConfig(), tmp_path, {"note": "other"}, write_manifest=False)
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert json.loads(before)["data_version"] == payloads(cases)[2]
