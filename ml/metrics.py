"""Metric functions. Pure, deterministic, unit-tested in tests/test_ml_metrics.py.

Outcome of one case, given its label and the decision taken:

============  ===============================  =====================================
label         correct                          unsafe
============  ===============================  =====================================
match         act on the target                act on any other candidate
ambiguous     clarify with the target listed   act on any candidate
no_match      abstain                          act on any candidate
============  ===============================  =====================================

Everything else is ``safe_other``: no action was taken on money, but the case
did not get the best handling (for example clarify on a clear match, which
costs the customer one extra turn, or abstain on a match, which is a handoff).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence

import numpy as np


def outcome(label: str, target: str | None, decision: str, top_k: Sequence[str]) -> str:
    if decision == "act":
        return "correct" if (label == "match" and top_k and top_k[0] == target) else "unsafe"
    if label == "ambiguous" and decision == "clarify" and target in top_k:
        return "correct"
    if label == "no_match" and decision == "abstain":
        return "correct"
    return "safe_other"


def reciprocal_rank(ranked_ids: Sequence[str], target: str) -> float:
    for i, tid in enumerate(ranked_ids):
        if tid == target:
            return 1.0 / (i + 1)
    return 0.0


def hit_at_k(ranked_ids: Sequence[str], target: str, k: int) -> float:
    return float(target in list(ranked_ids)[:k])


def ece(confidences: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    """Expected calibration error with equal-width bins."""
    c = np.asarray(confidences, dtype=float)
    y = np.asarray(labels, dtype=float)
    if len(c) == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(c, edges[1:-1], right=True), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.sum() / len(c) * abs(c[m].mean() - y[m].mean())
    return float(total)


def percentile(values: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q)) if len(values) else float("nan")


def bootstrap_ci(items: Sequence[dict], stat: Callable[[list[dict]], float], group_key: str = "group",
                 n_boot: int = 1000, seed: int = 7, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI, resampling whole groups (a Spanish case and its Portuguese twin move together)."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it[group_key], []).append(it)
    keys = sorted(groups)
    if not keys:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        sample = [it for k in (rng.choice(keys) for _ in keys) for it in groups[k]]
        v = stat(sample)
        if v == v:  # skip NaN draws (e.g. no match case in the resample)
            stats.append(v)
    if not stats:
        return float("nan"), float("nan")
    return float(np.percentile(stats, 100 * alpha / 2)), float(np.percentile(stats, 100 * (1 - alpha / 2)))


def selective_curve(confidences: Sequence[float], correct: Sequence[int], points: int = 11) -> list[dict]:
    """Coverage (share of cases acted on) vs selective accuracy (share of actions that were right)."""
    c = np.asarray(confidences, dtype=float)
    y = np.asarray(correct, dtype=float)
    out = []
    for t in np.quantile(c, np.linspace(0.0, 1.0, points)) if len(c) else []:
        m = c >= t
        out.append({"threshold": round(float(t), 4), "coverage": round(float(m.mean()), 4),
                    "selective_accuracy": round(float(y[m].mean()), 4) if m.any() else None,
                    "n_acted": int(m.sum())})
    return out
