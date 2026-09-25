"""Decision layer: thresholds and calibrators can only be fitted on their own split."""

from __future__ import annotations

import pytest

from ml import decision, disposition, evaluate
from ml.data import ranker_input, record
from ml.decision import Calibrator, CalibratedDecider, DecisionPolicy, decide, search_policy
from ml.disposition import DispositionDecider
from ml.rankers.rules import RuleRanker
from ml.scenarios.build import BuildConfig, build
from tests.ml_tests.synthetic import make_world


@pytest.fixture(scope="module")
def cases():
    customers, txs = make_world()
    built, _ = build(customers, txs, BuildConfig(n_es={"train": 36, "val": 18, "test": 12},
                                                 min_per_stratum={"train": 1, "val": 1, "test": 1}))
    return built


@pytest.fixture(scope="module")
def ranked(cases):
    rules = RuleRanker()
    return {s: [record(c, rules.rank(ranker_input(c), c["candidates"])) for c in cases[s]] for s in cases}


def test_decide_rule_order():
    ranked = [("a", 0.9), ("b", 0.2), ("c", 0.1), ("d", 0.0)]
    pol = DecisionPolicy(t_act=0.8, t_abstain=0.7)
    assert decide(ranked, 0.85, 0.1, pol) == {"decision": "act", "top_k": ["a"]}
    assert decide(ranked, 0.5, 0.1, pol) == {"decision": "clarify", "top_k": ["a", "b", "c"]}
    assert decide(ranked, 0.99, 0.75, pol)["decision"] == "abstain"
    assert decide([], 0.99, 0.0, pol)["decision"] == "abstain"


def test_calibrator_refuses_non_train_records(ranked):
    with pytest.raises(ValueError):
        Calibrator().fit(ranked["val"])
    with pytest.raises(ValueError):
        Calibrator().fit(ranked["test"])


def test_thresholds_refuse_non_val_records(ranked):
    for split in ("train", "test"):
        recs = ranked[split]
        with pytest.raises(ValueError):
            search_policy(recs, [0.5] * len(recs), [0.5] * len(recs))


def test_disposition_refuses_test_records(cases, ranked):
    items = lambda s: [(c, ranker_input(c), r["ranked"]) for c, r in zip(cases[s], ranked[s])]  # noqa: E731
    with pytest.raises(ValueError):
        DispositionDecider.fit(items("test"), items("val"), 0.01)
    with pytest.raises(ValueError):
        DispositionDecider.fit(items("train"), items("test"), 0.01)


def test_fitted_policy_respects_unsafe_cap_on_val(ranked):
    decider, info = CalibratedDecider.fit(ranked["train"], ranked["val"], max_unsafe_rate=0.05)
    assert info["val_unsafe_rate"] <= 0.05
    assert decider.policy.fitted_on == "val"


def test_evaluation_path_never_fits(monkeypatch, cases, ranked):
    decider, _ = CalibratedDecider.fit(ranked["train"], ranked["val"], max_unsafe_rate=0.05)

    def boom(*args, **kwargs):
        raise AssertionError("fitting during evaluation")

    monkeypatch.setattr(Calibrator, "fit", boom)
    monkeypatch.setattr(decision, "search_policy", boom)
    monkeypatch.setattr(disposition, "search_policy", boom)
    rows = evaluate.run_system(RuleRanker(), decider, cases["test"])
    assert len(rows) == len(cases["test"])
    assert {r["decision"] for r in rows} <= {"act", "clarify", "abstain"}
