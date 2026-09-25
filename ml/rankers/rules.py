"""Baseline: deterministic rules with hand-set weights, nothing fitted.

Score = weighted mean of per-cue agreement over the cues the parser found:
amount proximity (0.30), date proximity (0.25), merchant (0.25), type (0.10),
channel (0.10), city (0.05). Credits (deposits) are down-weighted because a
disputed charge is a debit. With no readable cue every candidate scores 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ml.features.pairwise import FEATURE_NAMES, candidate_features
from ml.rankers.protocol import coerce_features, order

IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
WEIGHTS = {"amount": 0.30, "date": 0.25, "merchant": 0.25, "type": 0.10, "channel": 0.10, "city": 0.05}
MERCHANT_FUZZY_MIN = 0.8
CREDIT_FACTOR = 0.2


def rule_score(f: list[float]) -> float:
    parts: dict[str, float] = {}
    if f[IDX["amt_given"]]:
        a = max(0.0, 1.0 - f[IDX["amt_logdiff"]] / math.log(1.5))
        if f[IDX["cur_given"]] and not f[IDX["cur_match"]]:
            a = 0.0
        parts["amount"] = a
    if f[IDX["date_given"]]:
        parts["date"] = max(0.0, 1.0 - f[IDX["date_dist"]] / 5.0)
    fuzzy = f[IDX["merch_max"]] if f[IDX["merch_max"]] >= MERCHANT_FUZZY_MIN else 0.0
    if f[IDX["text_merch_signal"]] >= MERCHANT_FUZZY_MIN or f[IDX["noun_given"]]:
        parts["merchant"] = max(fuzzy, f[IDX["noun_match"]])
    if f[IDX["type_given"]]:
        parts["type"] = f[IDX["type_match"]]
    if f[IDX["chan_given"]]:
        parts["channel"] = f[IDX["chan_match"]]
    if f[IDX["city_match"]]:
        parts["city"] = 1.0
    total = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / total if total else 0.0
    return score * (1.0 if f[IDX["is_debit"]] else CREDIT_FACTOR)


class RuleRanker:
    name = "rules_v1"

    def rank(self, features: Any, candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        if not candidates:
            return []
        parsed, report = coerce_features(features)
        rows = candidate_features(parsed, list(candidates), report)
        return order([c["transaction_id"] for c in candidates], [rule_score(r) for r in rows], candidates)
