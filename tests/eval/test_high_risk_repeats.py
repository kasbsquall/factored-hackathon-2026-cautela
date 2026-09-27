"""Run-to-run agreement of the high-risk repeats (eval/high_risk_repeats.py), on hand-built rows."""

from __future__ import annotations

import pytest

from eval.high_risk_repeats import agreement, preflight


def row(conv_id, category, outcome="handoff", code="security_event", correct=True, unsafe=()):
    return {"conv_id": conv_id, "category": category, "subcategory": "s", "outcome": outcome, "handoff_code": code,
            "correct": correct, "unsafe": list(unsafe)}


def test_identical_runs_agree_on_every_conversation():
    rows = [row("i1", "injection"), row("u1", "unauthorized"), row("h1", "human_request", code="customer_requested_human")]
    out = agreement([rows, [dict(r) for r in rows], [dict(r) for r in rows]])
    assert out["changed"] == []
    assert out["by_category"]["all"]["same_outcome_all_runs"]["k"] == 3
    assert out["by_category"]["all"]["same_outcome_all_runs"]["n"] == 3
    assert out["by_category"]["injection"]["unsafe_per_run"] == [0, 0, 0]


def test_a_changed_reason_code_counts_as_disagreement_and_is_listed_with_each_run():
    run1 = [row("a1", "adversarial"), row("a2", "adversarial", outcome="refusal", code=None)]
    run2 = [row("a1", "adversarial", code="out_of_scope"), row("a2", "adversarial", outcome="refusal", code=None)]
    run3 = [row("a1", "adversarial", outcome="resolved", code=None, correct=False, unsafe=["injection_followed"]),
            row("a2", "adversarial", outcome="refusal", code=None)]
    out = agreement([run1, run2, run3])
    cat = out["by_category"]["adversarial"]
    assert (cat["same_outcome_all_runs"]["k"], cat["same_outcome_all_runs"]["n"]) == (1, 2)
    assert cat["unsafe_per_run"] == [0, 0, 1] and cat["unsafe_conversations_per_run"] == [[], [], ["a1"]]
    assert cat["correct_per_run"] == [2, 2, 1]
    assert out["changed"] == [{"conv_id": "a1", "category": "adversarial", "subcategory": "s",
                               "outcomes": ["handoff:security_event", "handoff:out_of_scope", "resolved"],
                               "correct": [True, True, False], "unsafe": [[], [], ["injection_followed"]]}]


def test_categories_without_conversations_are_left_out():
    out = agreement([[row("i1", "injection")], [row("i1", "injection")]])
    assert set(out["by_category"]) == {"injection", "all"}


def test_preflight_refuses_before_any_paid_run_when_the_estimate_exceeds_the_cap():
    assert preflight(0.50, 242, 3) <= 0.50
    with pytest.raises(SystemExit, match="nothing run"):
        preflight(0.01, 242, 3)
