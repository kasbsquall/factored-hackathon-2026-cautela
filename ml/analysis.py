"""Aggregate per-case evaluation results into the metrics reported in results.json."""

from __future__ import annotations

from collections import Counter

import numpy as np

from ml.metrics import bootstrap_ci, ece, percentile, selective_curve

BREAKDOWNS = ("language", "country", "segment", "family_split", "family", "label", "info_level", "pool_bucket",
              "country_x_family_split")


def pool_bucket(n: int) -> str:
    return "3" if n <= 3 else ("4-5" if n <= 5 else "6+")


def _mean(values) -> float:
    return float(np.mean(values)) if len(values) else float("nan")


def top1(rows: list[dict]) -> float:
    return _mean([r["hit1"] for r in rows if r["label"] == "match"])


def top3(rows: list[dict]) -> float:
    return _mean([r["hit3"] for r in rows if r["label"] == "match"])


def top3_pool4(rows: list[dict]) -> float:
    return _mean([r["hit3"] for r in rows if r["label"] == "match" and r["pool_size"] >= 4])


def mrr(rows: list[dict]) -> float:
    return _mean([r["rr"] for r in rows if r["label"] == "match"])


def correct_rate(rows: list[dict]) -> float:
    return _mean([r["outcome"] == "correct" for r in rows])


def unsafe_rate(rows: list[dict]) -> float:
    return _mean([r["outcome"] == "unsafe" for r in rows])


def safe_auto_rate(rows: list[dict]) -> float:
    """Correct action taken without a clarifying turn, over all in-scope cases."""
    return _mean([r["decision"] == "act" and r["outcome"] == "correct" for r in rows])


def ece_all(rows: list[dict]) -> float:
    return ece([r["confidence"] for r in rows], [r["act_correct"] for r in rows])


HEADLINE = {"top1_accuracy": top1, "top3_recall": top3, "top3_recall_pool_ge4": top3_pool4, "mrr": mrr,
            "ece": ece_all, "correct_decision_rate": correct_rate, "unsafe_rate": unsafe_rate,
            "safe_automated_resolution_rate": safe_auto_rate}


def summarize(rows: list[dict], n_boot: int = 1000, with_ci: bool = True) -> dict:
    n = len(rows)
    labels = Counter(r["label"] for r in rows)
    out: dict = {"n_cases": n, "n_by_label": dict(sorted(labels.items())),
                 "n_groups": len({r["group"] for r in rows})}
    for name, fn in HEADLINE.items():
        v = fn(rows)
        entry = {"value": round(v, 4) if v == v else None}
        if with_ci:
            lo, hi = bootstrap_ci(rows, fn, n_boot=n_boot)
            entry["ci95"] = [round(lo, 4), round(hi, 4)] if lo == lo else None
        out[name] = entry
    decisions = Counter(r["decision"] for r in rows)
    out["decisions"] = dict(sorted(decisions.items()))
    out["automation_attempted_share"] = round(decisions.get("act", 0) / n, 4) if n else None
    out["unsafe"] = {"count": sum(r["outcome"] == "unsafe" for r in rows), "denominator": n,
                     "acted_denominator": decisions.get("act", 0)}
    per_label = {}
    for lab in sorted(labels):
        sub = [r for r in rows if r["label"] == lab]
        per_label[lab] = {"n": len(sub), "decisions": dict(sorted(Counter(r["decision"] for r in sub).items())),
                          "outcomes": dict(sorted(Counter(r["outcome"] for r in sub).items()))}
    out["per_label"] = per_label
    amb = [r for r in rows if r["label"] == "ambiguous"]
    nm = [r for r in rows if r["label"] == "no_match"]
    out["clarify_correct_on_ambiguous"] = {"count": sum(r["outcome"] == "correct" for r in amb), "denominator": len(amb)}
    out["abstain_correct_on_no_match"] = {"count": sum(r["outcome"] == "correct" for r in nm), "denominator": len(nm)}
    out["asked_on_no_match"] = {"count": sum(r["decision"] == "clarify" for r in nm), "denominator": len(nm)}
    # abstain = transfer to a human agent in the service; clarify keeps the case in the conversation
    out["containment_rate"] = round(sum(r["decision"] != "abstain" for r in rows) / n, 4) if n else None
    out["missed_transfers"] = {"count": sum(r["decision"] != "abstain" for r in nm), "denominator": len(nm)}
    in_scope = [r for r in rows if r["label"] != "no_match"]
    out["unnecessary_transfers"] = {"count": sum(r["decision"] == "abstain" for r in in_scope),
                                    "denominator": len(in_scope)}
    rec = [r for r in rows if r["label"] == "match" and r.get("recall_error")]
    out["recall_error_match"] = {"n": len(rec), "top1_accuracy": round(top1(rec), 4) if rec else None,
                                 "acted_correctly": sum(r["decision"] == "act" and r["outcome"] == "correct" for r in rec)}
    lat = [r["latency_ms"] for r in rows]
    out["latency_ms"] = {"p50": round(percentile(lat, 50), 3), "p95": round(percentile(lat, 95), 3)}
    return out


def breakdowns(rows: list[dict]) -> dict:
    out = {}
    for key in BREAKDOWNS:
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(str(r[key]), []).append(r)
        out[key] = {}
        for g, sub in sorted(groups.items()):
            n_match = sum(r["label"] == "match" for r in sub)
            out[key][g] = {"n": len(sub), "n_match": n_match,
                           "top1_accuracy": round(top1(sub), 4) if n_match else None,
                           "mrr": round(mrr(sub), 4) if n_match else None,
                           "correct_decision_rate": round(correct_rate(sub), 4),
                           "unsafe": {"count": sum(r["outcome"] == "unsafe" for r in sub), "denominator": len(sub)}}
    return out


def paired_difference(a: list[dict], b: list[dict], fn, n_boot: int = 1000, seed: int = 11) -> dict:
    """Bootstrap CI of fn(b) - fn(a) on the same cases, resampling groups jointly."""
    by_case = {r["case_id"]: r for r in a}
    pairs = [{"group": r["group"], "a": by_case[r["case_id"]], "b": r} for r in b if r["case_id"] in by_case]
    stat = lambda items: fn([p["b"] for p in items]) - fn([p["a"] for p in items])  # noqa: E731
    lo, hi = bootstrap_ci(pairs, stat, n_boot=n_boot, seed=seed)
    return {"diff": round(stat(pairs), 4), "ci95": [round(lo, 4), round(hi, 4)], "n_cases": len(pairs)}


def curve(rows: list[dict]) -> list[dict]:
    return selective_curve([r["confidence"] for r in rows], [r["act_correct"] for r in rows])


DISPARITY_METRICS = {"correct_decision_rate": correct_rate, "unsafe_rate": unsafe_rate,
                     "safe_automated_resolution_rate": safe_auto_rate}


def country_disparity(base: list[dict], proposed: list[dict], n_boot: int = 1000) -> dict:
    """Per country: each system's rate with a group bootstrap CI, the paired difference, and the max-min gap."""
    out: dict = {"by_country": {}, "gap_max_minus_min": {}}
    for country in sorted({r["country"] for r in proposed}):
        a = [r for r in base if r["country"] == country]
        b = [r for r in proposed if r["country"] == country]
        entry: dict = {"n": len(b)}
        for metric, fn in DISPARITY_METRICS.items():
            cell = {}
            for label, rows in (("baseline", a), ("proposed", b)):
                lo, hi = bootstrap_ci(rows, fn, n_boot=n_boot)
                cell[label] = {"value": round(fn(rows), 4), "ci95": [round(lo, 4), round(hi, 4)]}
            cell["proposed_minus_baseline"] = paired_difference(a, b, fn, n_boot=n_boot)
            entry[metric] = cell
        out["by_country"][country] = entry
    for metric in DISPARITY_METRICS:
        for label in ("baseline", "proposed"):
            vals = [e[metric][label]["value"] for e in out["by_country"].values()]
            out["gap_max_minus_min"].setdefault(metric, {})[label] = round(max(vals) - min(vals), 4)
    return out
