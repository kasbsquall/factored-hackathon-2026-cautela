"""Metric functions."""

from __future__ import annotations

import math

import pytest

from ml.metrics import bootstrap_ci, ece, hit_at_k, outcome, percentile, reciprocal_rank, selective_curve


@pytest.mark.parametrize("label,target,decision,top_k,expected", [
    ("match", "a", "act", ["a"], "correct"),
    ("match", "a", "act", ["b"], "unsafe"),
    ("match", "a", "clarify", ["b", "a"], "safe_other"),
    ("match", "a", "abstain", [], "safe_other"),
    ("ambiguous", "a", "clarify", ["b", "a", "c"], "correct"),
    ("ambiguous", "a", "clarify", ["b", "c", "d"], "safe_other"),
    ("ambiguous", "a", "act", ["a"], "unsafe"),
    ("no_match", None, "abstain", [], "correct"),
    ("no_match", None, "clarify", ["x"], "safe_other"),
    ("no_match", None, "act", ["x"], "unsafe"),
])
def test_outcome_table(label, target, decision, top_k, expected):
    assert outcome(label, target, decision, top_k) == expected


def test_reciprocal_rank_and_hits():
    ids = ["x", "y", "z"]
    assert reciprocal_rank(ids, "x") == 1.0
    assert reciprocal_rank(ids, "z") == pytest.approx(1 / 3)
    assert reciprocal_rank(ids, "missing") == 0.0
    assert hit_at_k(ids, "y", 1) == 0.0 and hit_at_k(ids, "y", 2) == 1.0


def test_ece_perfect_and_worst():
    assert ece([1.0, 1.0, 0.0, 0.0], [1, 1, 0, 0]) == pytest.approx(0.0)
    assert ece([1.0, 1.0], [0, 0]) == pytest.approx(1.0)
    # two bins: 0.25 with accuracy 0.5 and 0.75 with accuracy 0.5 -> 0.25 everywhere
    assert ece([0.25, 0.25, 0.75, 0.75], [1, 0, 1, 0]) == pytest.approx(0.25)
    assert math.isnan(ece([], []))


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert math.isnan(percentile([], 50))


def test_bootstrap_ci_brackets_the_estimate_and_resamples_groups():
    items = [{"group": f"g{i // 2}", "v": float(i % 3 == 0)} for i in range(200)]
    stat = lambda xs: sum(x["v"] for x in xs) / len(xs)  # noqa: E731
    lo, hi = bootstrap_ci(items, stat, n_boot=300)
    assert lo <= stat(items) <= hi
    constant = [{"group": "a", "v": 1.0}, {"group": "a", "v": 1.0}]
    assert bootstrap_ci(constant, stat, n_boot=50) == (1.0, 1.0)


def test_bootstrap_is_seeded():
    items = [{"group": str(i), "v": float(i % 2)} for i in range(50)]
    stat = lambda xs: sum(x["v"] for x in xs) / len(xs)  # noqa: E731
    assert bootstrap_ci(items, stat, n_boot=100) == bootstrap_ci(items, stat, n_boot=100)


def test_selective_curve_coverage_decreases():
    curve = selective_curve([0.1, 0.4, 0.6, 0.9, 0.95], [0, 0, 1, 1, 1], points=5)
    cov = [p["coverage"] for p in curve]
    assert cov == sorted(cov, reverse=True)
    assert curve[0]["coverage"] == 1.0
    assert curve[-1]["selective_accuracy"] == 1.0
