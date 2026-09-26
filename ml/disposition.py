"""Learned disposition model: does the description fit one charge, several, or none?

Ranking quality alone does not decide what to do. The case-level question is
whether exactly one candidate fits (act), several fit (clarify) or none fits
(abstain and hand off). A multinomial classifier predicts the scenario label
from case-level features:

* the ranker's score profile (top three scores, margin, how many candidates
  score above 0.5 and 0.2, pool size);
* which cues the parser found (amount, date, merchant, type, channel);
* how many candidates satisfy every parsed cue, and how many fail exactly one.

Fitted on train (ranker scores out-of-fold), model family chosen on val by log
loss, thresholds on val. ``P(match)`` is the act confidence and ``P(no_match)``
the abstain score.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.decision import DecisionPolicy, _require_split, decide, search_policy
from ml.features.pairwise import FEATURE_NAMES, candidate_features
from ml.rankers.protocol import coerce_features

CLASSES = ["ambiguous", "match", "no_match"]
IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
CASE_FEATURES = ["s1", "s2", "s3", "margin", "n_ge_050", "n_ge_020", "pool_size", "amt_cue", "date_cue",
                 "merch_cue", "type_cue", "chan_cue", "n_cues", "n_full", "n_near", "top1_full"]


def _cue_hits(row: list[float]) -> dict[str, bool]:
    hits = {}
    if row[IDX["amt_given"]]:
        hits["amount"] = bool(row[IDX["amt_within_25pct"]]) and not (row[IDX["cur_given"]] and not row[IDX["cur_match"]])
    if row[IDX["date_given"]]:
        hits["date"] = row[IDX["date_dist"]] <= 2
    if row[IDX["text_merch_signal"]] >= 0.8 or row[IDX["noun_given"]]:
        named = row[IDX["merch_max"]] >= 0.8 or bool(row[IDX["noun_match"]])
        hits["merchant"] = named and not row[IDX["merch_mismatch"]]
    if row[IDX["type_given"]]:
        hits["type"] = bool(row[IDX["type_match"]])
    if row[IDX["chan_given"]]:
        hits["channel"] = bool(row[IDX["chan_match"]])
    return hits


def case_features(inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> list[float]:
    parsed, report = coerce_features(inp)
    rows = candidate_features(parsed, candidates, report) if candidates else []
    scores = [s for _, s in ranked] + [0.0, 0.0, 0.0]
    hits = [_cue_hits(r) for r in rows]
    debit = [bool(r[IDX["is_debit"]]) for r in rows]
    cues = hits[0].keys() if hits else []
    n_cues = len(cues)
    full = [d and n_cues > 0 and all(h.values()) for h, d in zip(hits, debit)]
    near = [d and n_cues >= 3 and list(h.values()).count(False) == 1 for h, d in zip(hits, debit)]
    top_id = ranked[0][0] if ranked else None
    top_full = any(f for c, f in zip(candidates, full) if c["transaction_id"] == top_id)
    return [scores[0], scores[1], scores[2], scores[0] - scores[1], sum(s >= 0.5 for s, _ in zip(scores, ranked)),
            sum(s >= 0.2 for s, _ in zip(scores, ranked)), len(candidates) / 10.0,
            *(float(k in cues) for k in ("amount", "date", "merchant", "type", "channel")),
            float(n_cues), float(sum(full)), float(sum(near)), float(top_full)]


def _grid() -> dict:
    return {"multinomial_logreg_C1": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=3000)),
            "hgb_d3": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                                             l2_regularization=1.0, random_state=13)}


class DispositionDecider:
    def __init__(self, model, policy: DecisionPolicy, model_name: str):
        self.model, self.policy, self.model_name = model, policy, model_name
        self.name = f"disposition_{model_name}"

    def proba(self, inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> dict[str, float]:
        p = self.model.predict_proba(np.asarray([case_features(inp, candidates, ranked)]))[0]
        return dict(zip(self.model.classes_, p))

    def decide_case(self, inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> tuple[float, dict]:
        p = self.proba(inp, candidates, ranked)
        return float(p["match"]), decide(ranked, p["match"], p["no_match"], self.policy)

    def params(self) -> dict:
        return {"model": self.model_name, "features": CASE_FEATURES, "policy": self.policy.as_dict(),
                "act_score": "P(match)", "abstain_score": "P(no_match)"}

    @classmethod
    def fit(cls, train: list[tuple[dict, dict, list]], val: list[tuple[dict, dict, list]],
            max_unsafe_rate: float) -> tuple["DispositionDecider", dict]:
        """train / val items: (case, ranker input, ranked list). Train rankings must be out-of-fold."""
        _require_split([c for c, _, _ in train], "train", "the disposition model")
        _require_split([c for c, _, _ in val], "val", "the disposition model selection")
        x = np.asarray([case_features(i, c["candidates"], r) for c, i, r in train])
        y = np.asarray([c["label"] for c, _, _ in train])
        xv = np.asarray([case_features(i, c["candidates"], r) for c, i, r in val])
        yv = np.asarray([c["label"] for c, _, _ in val])
        selection, models = {}, {}
        for name, make in _grid().items():
            models[name] = make().fit(x, y)
            selection[name] = round(float(log_loss(yv, models[name].predict_proba(xv), labels=CLASSES)), 4)
        best = min(selection, key=lambda k: (selection[k], k))
        model = models[best]
        pv = model.predict_proba(xv)
        cls_idx = {c: i for i, c in enumerate(model.classes_)}
        records = [{"split": c["split"], "label": c["label"], "target": c["target_transaction_id"], "ranked": r}
                   for c, _, r in val]
        policy, info = search_policy(records, pv[:, cls_idx["match"]].tolist(), pv[:, cls_idx["no_match"]].tolist(),
                                     max_unsafe_rate)
        return cls(model, policy, best), {"model_selection_val_log_loss": selection, **info}
