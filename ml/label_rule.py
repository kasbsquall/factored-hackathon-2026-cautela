"""Baseline: the scenario label rule applied directly to the cues the parser reads. Nothing is fitted.

The labels are a rule over structured hints (``ml/scenarios/hints.py``): a candidate is *consistent* when it is an
approved or pending debit that satisfies every cue within the label tolerances, *near-consistent* when the
description has three or more cues and the candidate fails exactly one. The learned disposition model sees the same
counts as features (``n_full``, ``n_near``, ``top1_full`` in ``ml/disposition.py``), computed from the parsed text
with the same tolerances. This decider skips the classifier and applies the rule itself to the parsed cues:

* exactly one consistent candidate: act on it;
* two or more consistent candidates: clarify with them (at most ``K_CLARIFY``, in the ranker's order);
* none consistent and exactly one near-consistent: act on it (the labels' recall-error case);
* otherwise, including when the parser read no cue at all: abstain.

Its only inputs are the parser and the cue tests of ``ml.disposition._cue_hits``, so the gap between this rule and
the learned disposition measures what the classifier adds on top of the features that restate the label rule.
Confidence is 1 when it acts and 0 otherwise: the rule has no graded score to calibrate.
"""

from __future__ import annotations

from ml.decision import K_CLARIFY
from ml.disposition import _cue_hits
from ml.features.pairwise import candidate_features
from ml.rankers.protocol import coerce_features
from ml.scenarios.hints import DEBIT_TYPES, NEAR_MIN_CUES, TARGET_STATUSES


def _disputable(cand: dict) -> bool:
    return cand.get("transaction_type") in DEBIT_TYPES and cand.get("transaction_status") in TARGET_STATUSES


def rule_fits(inp: dict, candidates: list[dict]) -> tuple[int, list[str], list[str]]:
    """(number of cues read, consistent ids, near-consistent ids), in pool order."""
    parsed, report = coerce_features(inp)
    rows = candidate_features(parsed, candidates, report) if candidates else []
    hits = [_cue_hits(r) for r in rows]
    n_cues = len(hits[0]) if hits else 0
    full, near = [], []
    for cand, h in zip(candidates, hits):
        if not _disputable(cand) or n_cues == 0:
            continue
        misses = list(h.values()).count(False)
        if misses == 0:
            full.append(cand["transaction_id"])
        elif misses == 1 and n_cues >= NEAR_MIN_CUES:
            near.append(cand["transaction_id"])
    return n_cues, full, near


class LabelRuleDecider:
    name = "label_rule_on_parsed_cues"

    def decide_case(self, inp: dict, candidates: list[dict], ranked: list[tuple[str, float]]) -> tuple[float, dict]:
        _, full, near = rule_fits(inp, candidates)
        if len(full) == 1:
            return 1.0, {"decision": "act", "top_k": full}
        if len(full) >= 2:
            order = {t: i for i, (t, _) in enumerate(ranked)}
            return 0.0, {"decision": "clarify", "top_k": sorted(full, key=lambda t: order.get(t, len(order)))[:K_CLARIFY]}
        if len(near) == 1:
            return 1.0, {"decision": "act", "top_k": near}
        return 0.0, {"decision": "abstain", "top_k": []}

    def with_floor(self, floor: float) -> "LabelRuleDecider":
        return self  # it acts only on a rule fit; there is no confidence to floor

    def params(self) -> dict:
        return {"rule": "ml/scenarios/hints.py label rule on parsed cues", "k": K_CLARIFY,
                "near_min_cues": NEAR_MIN_CUES, "fitted": False}
