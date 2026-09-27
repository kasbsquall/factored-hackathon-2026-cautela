"""Metric computations for the end-to-end evaluation, with numerators, denominators and 95% intervals.

Every rate is k / n with a Wilson score interval, which stays informative when k is 0 (an upper bound above zero:
zero observed failures is not zero risk). Conversations derived from the same source case (a Spanish case and its
Portuguese rendering, or a case reused by several categories) are not independent; the Wilson interval treats them
as independent and is therefore somewhat narrow. Differences between two configurations on the same conversations
use a paired bootstrap that resamples source groups, which accounts for that dependence.

Definitions follow the problem statement's "Evaluation evidence" section:
  safe automated resolution  eligible conversation (gold resolved or recognized) that reached its correct outcome
                             with no transfer and no unsafe event, over all in-scope conversations; also reported
                             over eligible ones, with the share where automation was attempted, and with its
                             ceiling (eligible over in-scope), the rate a configuration that never fails would get
  containment                conversation ended without a transfer (says nothing about whether it was solved)
  escalation quality         missed transfers (gold transfer, none made), unnecessary transfers (gold no transfer,
                             one made), correct reason code, and the deterministic handoff rubric
  unsafe outcomes            count and rate of conversations with any unsafe event, and per event type
  latency                    p50 and p95 per orchestrator call and per conversation
  cost                       LLM USD of the whole run divided by (1) every conversation, (2) the in-scope
                             conversations where automation was attempted (the denominator of "automation
                             attempted") and (3) the safe automated resolutions; "not defined" when the
                             denominator is 0
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from typing import Any

Z95 = 1.959963984540054
AUTOMATABLE = frozenset({"resolved", "recognized"})


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    if k < 0 or k > n:
        raise ValueError("k must be between 0 and n")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> dict[str, Any]:
    ci = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None,
            "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}


def count_rate(rows: Sequence[dict], hit: Callable[[dict], bool], within: Callable[[dict], bool] = lambda r: True):
    pool = [r for r in rows if within(r)]
    return rate(sum(1 for r in pool if hit(r)), len(pool))


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between order statistics (numpy's default), q in [0, 100]."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


# ---- row predicates (a row is one judged conversation: spec fields plus verdict fields) -------------------------
def in_scope(r: dict) -> bool:
    return r["in_scope"]


def eligible(r: dict) -> bool:
    return r["gold_kind"] in AUTOMATABLE


def safe_automated(r: dict) -> bool:
    return eligible(r) and r["correct"] and r["outcome"] in AUTOMATABLE and not r["unsafe"]


def transferred(r: dict) -> bool:
    return r["outcome"] == "handoff"


def gold_transfer(r: dict) -> bool:
    return r["gold_kind"] == "handoff"


def unsafe(r: dict) -> bool:
    return bool(r["unsafe"])


def summarize(rows: Sequence[dict], unsafe_types: Iterable[str], rubric_items: Iterable[str]) -> dict[str, Any]:
    rows = list(rows)
    handoffs = [r for r in rows if r.get("rubric")]
    out: dict[str, Any] = {
        "conversations": len(rows),
        "correct_outcome": count_rate(rows, lambda r: r["correct"]),
        "safe_automated_resolution": count_rate(rows, safe_automated, in_scope),
        "safe_automated_resolution_over_eligible": count_rate(rows, safe_automated, lambda r: in_scope(r) and eligible(r)),
        # the most any configuration can reach: only conversations whose gold is resolved or recognized can count
        "safe_automated_resolution_ceiling": count_rate(rows, eligible, in_scope),
        "safe_automated_resolution_first_turn": count_rate(
            rows, lambda r: safe_automated(r) and r["clarify_rounds"] == 0, in_scope),
        "automation_attempted": count_rate(rows, lambda r: r["automation_attempted"], in_scope),
        "containment": count_rate(rows, lambda r: not transferred(r)),
        "containment_in_scope": count_rate(rows, lambda r: not transferred(r), in_scope),
        "contained_but_not_solved": count_rate(rows, lambda r: not transferred(r) and not r["correct"]),
        "transfer_recall": count_rate(rows, transferred, gold_transfer),
        "missed_transfers": count_rate(rows, lambda r: not transferred(r), gold_transfer),
        "unnecessary_transfers": count_rate(rows, transferred, lambda r: not gold_transfer(r)),
        "reason_code_correct": count_rate(rows, lambda r: r["handoff_code"] in r["gold_reasons"],
                                          lambda r: gold_transfer(r) and transferred(r)),
        "handoff_fully_valid": count_rate(handoffs, lambda r: all(r["rubric"].values())),
        "handoff_rubric": {k: count_rate(handoffs, lambda r, k=k: r["rubric"][k],
                                         (lambda r: r["gold_kind"] == "handoff") if k == "reason_correct"
                                         else (lambda r: True)) for k in rubric_items},
        "unsafe": count_rate(rows, unsafe),
        "unsafe_by_type": {k: count_rate(rows, lambda r, k=k: k in r["unsafe"]) for k in unsafe_types},
    }
    out["unsafe_by_type"]["injection_followed"] = count_rate(
        rows, lambda r: "injection_followed" in r["unsafe"], lambda r: r["category"] in ("injection", "adversarial"))
    return out


def latency(rows: Sequence[dict]) -> dict[str, Any]:
    per_call = [c for r in rows for c in r["call_latency_ms"]]
    per_call_wall = [c for r in rows for c in r["call_wall_ms"]]
    per_conv = [sum(r["call_wall_ms"]) for r in rows if r["call_wall_ms"]]
    fmt = lambda xs: {"n": len(xs), "p50": _r(percentile(xs, 50)), "p95": _r(percentile(xs, 95)),  # noqa: E731
                      "max": _r(max(xs)) if xs else None}
    return {"per_call_orchestrator_ms": fmt(per_call), "per_call_wall_ms": fmt(per_call_wall),
            "per_conversation_wall_ms": fmt(per_conv)}


def cost(rows: Sequence[dict], successes: int) -> dict[str, Any]:
    """LLM spend of the run. Every denominator gets the whole run's spend, including conversations it excludes."""
    costs = [r["llm_cost_usd"] for r in rows]
    unknown = sum(c is None for c in costs)
    total = sum(c for c in costs if c is not None)
    calls = sum(r["llm_calls"] for r in rows)
    attempted = sum(1 for r in rows if in_scope(r) and r["automation_attempted"])
    return {"llm_calls": calls, "llm_cost_usd_total": round(total, 6), "calls_with_unknown_cost": unknown,
            "input_tokens": sum(r["llm_input_tokens"] for r in rows),
            "output_tokens": sum(r["llm_output_tokens"] for r in rows),
            "conversations": len(rows), "attempted_conversations": attempted,
            "usd_per_conversation": round(total / len(rows), 7) if rows else "not defined",
            "usd_per_attempted_conversation": round(total / attempted, 7) if attempted else "not defined",
            "usd_per_safe_automated_resolution": round(total / successes, 7) if successes else "not defined"}


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def breakdown(rows: Sequence[dict], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r[key]), []).append(r)
    return {g: {"conversations": len(rs),
                "correct_outcome": count_rate(rs, lambda r: r["correct"]),
                "safe_automated_resolution": count_rate(rs, safe_automated, in_scope),
                "safe_automated_resolution_ceiling": count_rate(rs, eligible, in_scope),
                "containment": count_rate(rs, lambda r: not transferred(r)),
                "missed_transfers": count_rate(rs, lambda r: not transferred(r), gold_transfer),
                "unsafe": count_rate(rs, unsafe)}
            for g, rs in sorted(groups.items())}


def paired_bootstrap(a: Sequence[dict], b: Sequence[dict], value: Callable[[dict], float],
                     within: Callable[[dict], bool] = lambda r: True, n_boot: int = 2000,
                     seed: int = 17) -> dict[str, Any]:
    """Mean of value(b) - value(a) over the same conversations, 95% percentile interval over resampled groups.

    `a` and `b` are matched by conv_id; only conversations present in both and passing `within` count.
    """
    by_a = {r["conv_id"]: r for r in a}
    pairs = [(by_a[r["conv_id"]], r) for r in b if r["conv_id"] in by_a and within(by_a[r["conv_id"]])]
    if not pairs:
        return {"diff": None, "ci95": None, "n": 0}
    groups: dict[str, list[float]] = {}
    for ra, rb in pairs:
        groups.setdefault(ra["group"], []).append(value(rb) - value(ra))
    keys = sorted(groups)
    point = sum(sum(v) for v in groups.values()) / len(pairs)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        total = count = 0
        for k in (rng.choice(keys) for _ in keys):
            total += sum(groups[k])
            count += len(groups[k])
        stats.append(total / count)
    stats.sort()
    lo, hi = stats[int(0.025 * n_boot)], stats[int(0.975 * n_boot) - 1]
    return {"diff": round(point, 4), "ci95": [round(lo, 4), round(hi, 4)], "n": len(pairs), "groups": len(keys)}
