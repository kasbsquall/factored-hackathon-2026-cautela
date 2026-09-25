"""Decision layer: act on one charge, ask the customer to pick (clarify), or abstain.

Every decider turns a ranking into (act score, abstain score) and applies one rule:
abstain when the abstain score reaches ``t_abstain``, else act when the act score
reaches ``t_act``, else clarify with the top ``k`` candidates.

* ``FixedRuleDecider``: the hand-set rule of agent/tools/ranking.py, nothing fitted.
* ``CalibratedDecider``: a calibrator maps the ranking summary (top score, margin,
  pool size) to P(acting on the top candidate is correct), fitted on ``train``
  (out-of-fold scores for the learned ranker); abstain score = 1 - top score.
* ``DispositionDecider`` (ml/disposition.py): P(match) and P(no_match) from a
  case-level classifier.

Thresholds are chosen on ``val`` only by ``search_policy``: maximize the rate of
correct decisions subject to an unsafe-action rate at or below
``max_unsafe_rate``. ``Calibrator.fit`` and ``search_policy`` refuse records from
any other split, so the test split cannot reach them through any code path.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression

from ml.metrics import outcome

K_CLARIFY = 3


def summary(ranked: list[tuple[str, float]]) -> list[float]:
    s1 = ranked[0][1] if ranked else 0.0
    s2 = ranked[1][1] if len(ranked) > 1 else 0.0
    return [s1, s1 - s2, math.log(max(1, len(ranked)))]


def act_correct(rec: dict) -> int:
    return int(rec["label"] == "match" and bool(rec["ranked"]) and rec["ranked"][0][0] == rec["target"])


def _require_split(records: list[dict], split: str, what: str) -> None:
    bad = {r["split"] for r in records if r["split"] != split}
    if bad:
        raise ValueError(f"{what} may only be fitted on '{split}' records, got {sorted(bad)}")


class Calibrator:
    def __init__(self) -> None:
        self.model = LogisticRegression(C=1.0, max_iter=1000)

    def fit(self, records: list[dict]) -> "Calibrator":
        _require_split(records, "train", "the calibrator")
        x = np.array([summary(r["ranked"]) for r in records])
        y = np.array([act_correct(r) for r in records])
        self.model.fit(x, y)
        return self

    def predict(self, ranked: list[tuple[str, float]]) -> float:
        return float(self.model.predict_proba(np.array([summary(ranked)]))[0, 1])

    def params(self) -> dict:
        return {"coef": self.model.coef_.ravel().round(4).tolist(),
                "intercept": round(float(self.model.intercept_[0]), 4),
                "features": ["top_score", "margin", "log_pool_size"]}


@dataclass(frozen=True)
class DecisionPolicy:
    """abstain if abstain_score >= t_abstain; else act if act_score >= t_act; else clarify with top k."""

    t_act: float
    t_abstain: float
    k: int = K_CLARIFY
    max_unsafe_rate: float = 0.01
    fitted_on: str = "val"

    def as_dict(self) -> dict:
        return asdict(self)


def decide(ranked: list[tuple[str, float]], act_score: float, abstain_score: float, policy: DecisionPolicy) -> dict:
    top_k = [tid for tid, _ in ranked[: policy.k]]
    if not ranked or abstain_score >= policy.t_abstain:
        return {"decision": "abstain", "top_k": []}
    if act_score >= policy.t_act:
        return {"decision": "act", "top_k": top_k[:1]}
    return {"decision": "clarify", "top_k": top_k}


def _rates(records: list[dict], acts: list[float], abstains: list[float], policy: DecisionPolicy) -> tuple[float, float]:
    correct = unsafe = 0
    for r, a, b in zip(records, acts, abstains):
        d = decide(r["ranked"], a, b, policy)
        o = outcome(r["label"], r["target"], d["decision"], d["top_k"])
        correct += o == "correct"
        unsafe += o == "unsafe"
    return correct / len(records), unsafe / len(records)


def search_policy(records: list[dict], acts: list[float], abstains: list[float],
                  max_unsafe_rate: float = 0.01) -> tuple[DecisionPolicy, dict]:
    """Grid search on val: maximize correct decisions subject to unsafe rate <= max_unsafe_rate."""
    _require_split(records, "val", "decision thresholds")
    abstain_grid = sorted({1.01, *np.quantile(np.asarray(abstains), np.linspace(0.4, 1.0, 31)).round(4).tolist()})
    act_grid = np.linspace(0.30, 0.995, 140).round(4).tolist()
    best = None
    for ta in abstain_grid:
        for tc in act_grid:
            pol = DecisionPolicy(t_act=tc, t_abstain=ta, max_unsafe_rate=max_unsafe_rate)
            correct, unsafe = _rates(records, acts, abstains, pol)
            if unsafe > max_unsafe_rate:
                continue
            key = (correct, -unsafe, -tc)
            if best is None or key > best[0]:
                best = (key, pol, correct, unsafe)
    if best is None:  # no feasible point: never act, never abstain on a non-empty ranking
        pol = DecisionPolicy(t_act=1.01, t_abstain=1.01, max_unsafe_rate=max_unsafe_rate)
        best = (None, pol, *_rates(records, acts, abstains, pol))
    return best[1], {"val_correct_rate": round(best[2], 4), "val_unsafe_rate": round(best[3], 4),
                     "grid_size": len(abstain_grid) * len(act_grid)}


# ------------------------------------------------------------------ deciders

class FixedRuleDecider:
    """The hand-set rule in agent/tools/ranking.py: clarify if top < 0.60 or margin < 0.15, else act.

    Constants are copied, not imported, so the offline evaluation does not depend on the agent package;
    tests/test_ml_rankers.py checks they still agree when the agent module is importable.
    """

    name = "fixed_top0.60_margin0.15"
    MIN_TOP, MIN_MARGIN = 0.60, 0.15

    def decide_case(self, inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> tuple[float, dict]:
        s1, margin, _ = summary(ranked)
        if not ranked:
            return 0.0, {"decision": "abstain", "top_k": []}
        if s1 < self.MIN_TOP or margin < self.MIN_MARGIN:
            return s1, {"decision": "clarify", "top_k": [t for t, _ in ranked[:K_CLARIFY]]}
        return s1, {"decision": "act", "top_k": [ranked[0][0]]}

    def params(self) -> dict:
        return {"min_top": self.MIN_TOP, "min_margin": self.MIN_MARGIN, "fitted": False}


class CalibratedDecider:
    """Calibrated P(act is correct) from the ranking summary; abstain when the top score is low."""

    def __init__(self, calibrator: Calibrator, policy: DecisionPolicy):
        self.calibrator, self.policy, self.name = calibrator, policy, "calibrated_thresholds"

    def decide_case(self, inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> tuple[float, dict]:
        conf = self.calibrator.predict(ranked)
        return conf, decide(ranked, conf, 1.0 - (ranked[0][1] if ranked else 0.0), self.policy)

    def params(self) -> dict:
        return {"calibrator": self.calibrator.params(), "policy": self.policy.as_dict(),
                "abstain_score": "1 - top_score"}

    @classmethod
    def fit(cls, train_records: list[dict], val_records: list[dict], max_unsafe_rate: float) -> tuple["CalibratedDecider", dict]:
        cal = Calibrator().fit(train_records)
        acts = [cal.predict(r["ranked"]) for r in val_records]
        abst = [1.0 - (r["ranked"][0][1] if r["ranked"] else 0.0) for r in val_records]
        policy, info = search_policy(val_records, acts, abst, max_unsafe_rate)
        return cls(cal, policy), info
