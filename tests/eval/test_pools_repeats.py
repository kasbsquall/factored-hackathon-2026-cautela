"""Pool-size buckets (eval/pools.py) and the security repeat summary (eval/repeat.py), on hand-built rows."""

from __future__ import annotations

from eval.pools import bucket
from eval.repeat import security_repeats


def test_pool_buckets_match_the_reported_ranges():
    assert [bucket(n) for n in (0, 1, 2, 3, 4, 23)] == ["0", "1", "2-3", "2-3", "4+", "4+"]


def row(conv_id, category, correct=True, unsafe=(), outcome="handoff", code="security_event", cost=0.001):
    return {"conv_id": conv_id, "category": category, "correct": correct, "unsafe": list(unsafe),
            "outcome": outcome, "handoff_code": code, "llm_cost_usd": cost}


def test_security_repeats_counts_per_run_and_outcome_stability():
    run1 = [row("i1", "injection"), row("i2", "injection", correct=False, outcome="pending", code=None),
            row("u1", "unauthorized"), row("d1", "dispute", outcome="resolved", code=None)]
    run2 = [row("i1", "injection", correct=False, unsafe=["injection_followed"], outcome="resolved", code=None),
            row("i2", "injection", correct=False, outcome="pending", code=None), row("u1", "unauthorized")]
    out = security_repeats([{"run_id": "r1", "rows": run1}, {"run_id": "r2", "rows": run2}])
    first, second = out["runs"]
    assert first["conversations"] == 3 and first["injection"] == {
        "conversations": 2, "correct": 1, "unsafe": 0, "unsafe_conversations": []}
    assert second["injection"]["unsafe"] == 1 and second["injection"]["unsafe_conversations"] == ["i1"]
    assert first["unauthorized"]["correct"] == 1 and first["cost_usd"] == 0.003
    assert out["same_outcome_all_runs"] == {"k": 2, "n": 3}  # i1 changed outcome, i2 and u1 repeated
