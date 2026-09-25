"""Recompute a case's label from its stored hints and candidates with the documented rule."""

from __future__ import annotations

from datetime import date

from ml.scenarios.hints import consistent_ids, near_consistent_ids


def as_pool(case: dict) -> list[dict]:
    return [{**c, "date": date.fromisoformat(c["transaction_date"][:10])} for c in case["candidates"]]


def recomputed_label(case: dict) -> tuple[str, list[str], list[str]]:
    pool = as_pool(case)
    full = consistent_ids(case["hints"], pool)
    near = near_consistent_ids(case["hints"], pool)
    if len(full) == 1:
        return "match", full, near
    if len(full) >= 2:
        return "ambiguous", full, near
    if len(near) == 1:
        return "match", full, near
    return ("no_match" if not near else "unlabeled"), full, near


def audit(case: dict) -> list[str]:
    """Problems found; empty when the stored label follows from the rule."""
    label, full, near = recomputed_label(case)
    problems = []
    if label != case["label"]:
        problems.append(f"{case['case_id']}: stored {case['label']}, rule gives {label}")
    if sorted(full) != sorted(case["consistent_ids"]):
        problems.append(f"{case['case_id']}: consistent ids differ")
    target = case["target_transaction_id"]
    if case["label"] == "match" and target not in (full or near):
        problems.append(f"{case['case_id']}: target is not the fitting candidate")
    if case["label"] == "ambiguous" and target not in full:
        problems.append(f"{case['case_id']}: target not among consistent candidates")
    if case["label"] == "no_match" and target is not None:
        problems.append(f"{case['case_id']}: no_match case has a target")
    return problems
