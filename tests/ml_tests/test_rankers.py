"""Rankers: valid output, protocol compatibility, parser cues, LLM payload masking."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from sklearn.linear_model import LogisticRegression

from ml.data import ranker_input
from ml.features.parse import parse_description, parse_number
from ml.rankers.learned import LearnedRanker
from ml.rankers.protocol import CandidateRanker
from ml.rankers.rules import RuleRanker
from ml.scenarios.build import BuildConfig, build
from ml.train import design_matrix
from tests.ml_tests.synthetic import make_world


@pytest.fixture(scope="module")
def cases():
    customers, txs = make_world()
    built, _ = build(customers, txs, BuildConfig(n_es={"train": 30, "val": 6, "test": 12},
                                                 min_per_stratum={"train": 1, "val": 1, "test": 1}))
    return built


@pytest.fixture(scope="module")
def rankers(cases):
    x, y, _ = design_matrix(cases["train"])
    return [RuleRanker(), LearnedRanker(LogisticRegression(max_iter=1000).fit(x, y), name="learned_test")]


def test_rankers_return_each_pool_id_once_with_scores_in_unit_range(cases, rankers):
    for ranker in rankers:
        for case in cases["test"]:
            ranked = ranker.rank(ranker_input(case), case["candidates"])
            ids = [t for t, _ in ranked]
            assert sorted(ids) == sorted(c["transaction_id"] for c in case["candidates"])
            assert all(0.0 <= s <= 1.0 for _, s in ranked)
            assert [s for _, s in ranked] == sorted((s for _, s in ranked), reverse=True)


def test_rankers_ignore_candidate_order(cases, rankers):
    case = cases["test"][0]
    for ranker in rankers:
        a = ranker.rank(ranker_input(case), case["candidates"])
        b = ranker.rank(ranker_input(case), list(reversed(case["candidates"])))
        assert a == b


def test_empty_pool_gives_empty_ranking(rankers):
    for ranker in rankers:
        assert ranker.rank({"text": "me cobraron 500", "report_date": "2026-01-10"}, []) == []


def test_rankers_satisfy_the_protocol_and_accept_agent_style_features(cases, rankers):
    case = cases["test"][0]
    feats = SimpleNamespace(text=case["description"], amount=None, currency=None, date_hint=None,
                            date_tolerance_days=3, merchant_hint=None, report_date=case["report_date"])
    for ranker in rankers:
        assert isinstance(ranker, CandidateRanker)
        assert len(ranker.rank(feats, case["candidates"])) == len(case["candidates"])


def test_compatible_with_agent_protocol_when_available(rankers):
    ranking = pytest.importorskip("agent.tools.ranking")
    from ml.decision import FixedRuleDecider

    for ranker in rankers:
        assert isinstance(ranker, ranking.CandidateRanker)
    assert (FixedRuleDecider.MIN_TOP, FixedRuleDecider.MIN_MARGIN) == (ranking.AMBIGUITY_MIN_TOP,
                                                                       ranking.AMBIGUITY_MIN_MARGIN)


def test_structured_amount_overrides_text():
    cands = [{"transaction_id": "a", "transaction_date": "2026-01-05 10:00:00", "amount": 100.0, "currency": "USD",
              "transaction_type": "Purchase", "channel": "POS", "merchant_name": None},
             {"transaction_id": "b", "transaction_date": "2026-01-05 11:00:00", "amount": 900.0, "currency": "USD",
              "transaction_type": "Purchase", "channel": "POS", "merchant_name": None}]
    ranked = RuleRanker().rank({"text": "un cargo raro", "report_date": "2026-01-10", "amount": 880}, cands)
    assert ranked[0][0] == "b"


@pytest.mark.parametrize("tok,value", [("1.627.495,96", 1627495.96), ("10,000", 10000.0), ("1,8", 1.8),
                                       ("467.08", 467.08), ("26", 26.0), ("1.500", 1500.0)])
def test_parse_number(tok, value):
    assert parse_number(tok) == pytest.approx(value)


def test_parser_reads_seen_phrasing_in_both_languages():
    report = date(2026, 3, 18)  # a Wednesday
    p = parse_description("Me hicieron un retiro de como 1,5 millones de pesos la semana pasada en un cajero", report)
    assert p.amount == pytest.approx(1_500_000) and p.currency == "PESOS"
    assert (p.date_lo, p.date_hi) == (date(2026, 3, 9), date(2026, 3, 15))
    assert p.types == ["Withdrawal"] and p.channels == ["ATM"]
    q = parse_description("Fizeram uma cobrança de uns 500 dólares ontem pelo aplicativo", report)
    assert q.amount == 500 and q.currency == "USD" and q.date_lo == date(2026, 3, 17) and q.channels == ["App"]


def test_parser_leaves_unknown_cues_empty():
    # "verdes" (dollars) and "el finde" are deliberately not in the lexicon
    p = parse_description("me cayó un cargo de como quinientos verdes el finde", date(2026, 3, 18))
    assert p.amount == 500 and p.currency is None and p.date_lo is None


def test_llm_payload_masks_pii_and_hides_ids():
    pytest.importorskip("agent.security.pii")
    from ml.rankers.llm import LLMRanker, build_payload

    cands = [{"transaction_id": "TRX-SECRET-1", "transaction_date": "2026-01-02 10:00:00", "amount": 50.0,
              "currency": "USD", "transaction_type": "Purchase", "channel": "App", "merchant_name": "Uber",
              "transaction_city": "Guadalajara", "customer_id": "CLI-XYZ"}]
    text = "Soy Juan Perez, mi correo es juan.perez@example.com, me cobraron 50 dolares en Uber"
    message, labels = build_payload({"text": text, "report_date": "2026-01-05"}, cands, known_names=["Juan Perez"])
    assert "juan.perez@example.com" not in message and "Juan Perez" not in message
    assert "TRX-SECRET-1" not in message and "CLI-XYZ" not in message and labels == {"C1": "TRX-SECRET-1"}

    class FakeClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                body = json.dumps({"scores": [{"candidate": "C1", "score": 0.9}, {"candidate": "C9", "score": 1}]})
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=body)],
                                       usage=SimpleNamespace(input_tokens=1000, output_tokens=100))

    ranker = LLMRanker(client=FakeClient())
    assert ranker.rank({"text": text, "report_date": "2026-01-05"}, cands) == [("TRX-SECRET-1", 0.9)]
    assert ranker.cost_usd() == pytest.approx((1000 * 2.0 + 100 * 10.0) / 1e6)
