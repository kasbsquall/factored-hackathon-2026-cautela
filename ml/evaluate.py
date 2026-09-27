"""Evaluate every ranker on the same held-out test split.

    uv run python -m ml.evaluate            # rules + learned (+ LLM when a key is configured)
    uv run python -m ml.evaluate --no-llm
    uv run python -m ml.evaluate --split test_fresh   # once: refuses to run again after results_fresh.json exists

Reads the artifacts written by ml/train.py; thresholds and calibrators come from
there and are never refitted on test. Writes ml/reports/results.json (or
results_fresh.json for test_fresh; every number in results.md comes from them)
and one MLflow run per ranker. test_fresh is checked against the sha256 frozen
in the manifest before anything runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import random
import time
from pathlib import Path

from ml import tracking
from ml.analysis import (breakdowns, correct_rate, country_disparity, curve, mrr, paired_by_group,
                         paired_difference, pool_bucket, safe_auto_rate, summarize, top1, unsafe_rate)
from ml.data import CASES_DIR, MODELS_DIR, REPORTS_DIR, data_version, group_of, load_cases, ranker_input, record
from ml.decision import CalibratedDecider, act_correct
from ml.metrics import hit_at_k, outcome, reciprocal_rank
from ml.rankers.learned import LearnedRanker
from ml.rankers.rules import RuleRanker
from ml.train import MAX_UNSAFE_RATE

PROPOSED = "learned_ranker_disposition"
LLM_RUNS = 3
LLM_SUBSET = {"train": 120, "val": 150, "test": 200}
FRESH = "test_fresh"
RESULTS = {"test": "results.json", FRESH: "results_fresh.json"}


def check_fresh_frozen(out: Path) -> None:
    """test_fresh runs once, on the exact file hashed in the manifest before any model saw it."""
    if out.exists():
        raise SystemExit(f"{out} exists: {FRESH} is evaluated once and never re-run after its results are read")
    manifest = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))
    path = CASES_DIR / f"{FRESH}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found: rebuild it with `uv run python -m ml.scenarios.build --verify`")
    sha = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if FRESH not in manifest or sha != manifest[FRESH]["file_sha256"]:
        raise SystemExit(f"{path} does not match the sha256 frozen in manifest.json")


def case_row(case: dict, ranked: list, conf: float, d: dict, ms: float) -> dict:
    """One evaluated case. ``confidence`` is the system's act confidence (calibrated, or P(match))."""
    ids = [t for t, _ in ranked]
    target = case["target_transaction_id"]
    rec = record(case, ranked)
    return {
        "case_id": case["case_id"], "group": group_of(case), "language": case["language"],
        "country": case["country"], "segment": case["segment"], "family": case["family"],
        "family_split": "held_out" if case["family_heldout"] else "seen", "label": case["label"],
        "country_x_family_split": f"{case['country']}/{'held_out' if case['family_heldout'] else 'seen'}",
        "info_level": case["info_level"], "recall_error": case.get("recall_error"),
        "pool_size": len(case["candidates"]), "pool_bucket": pool_bucket(len(case["candidates"])),
        "decision": d["decision"], "top_k": d["top_k"], "confidence": round(conf, 6),
        "outcome": outcome(case["label"], target, d["decision"], d["top_k"]), "act_correct": act_correct(rec),
        "rr": reciprocal_rank(ids, target) if target else None, "hit1": hit_at_k(ids, target, 1) if target else None,
        "hit3": hit_at_k(ids, target, 3) if target else None, "latency_ms": round(ms, 3),
        "ranked_top": [[t, round(s, 4)] for t, s in ranked[:5]],
    }


def run_system(ranker, decider, cases: list[dict]) -> list[dict]:
    """Rank and decide every case; latency covers parsing, ranking and the decision."""
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        inp = ranker_input(case)
        ranked = ranker.rank(inp, case["candidates"])
        conf, d = decider.decide_case(inp, case["candidates"], ranked)
        rows.append(case_row(case, ranked, conf, d, (time.perf_counter() - t0) * 1000))
    return rows


def describe(case: dict, row: dict) -> dict:
    by_id = {c["transaction_id"]: c for c in case["candidates"]}
    # No organizer transaction values (date, amount, merchant) and no description rendered from them: the reports
    # are committed, the organizer rows are not.
    top = [{k: by_id[t].get(k) for k in ("currency", "transaction_type", "channel")}
           | {"is_target": t == case["target_transaction_id"], "score": s}
           for t, s in row["ranked_top"][:3]]
    return {"case_id": case["case_id"], "label": case["label"],
            "family": case["family"], "recall_error": case.get("recall_error"), "report_date": case["report_date"],
            "decision": row["decision"], "confidence": row["confidence"], "outcome": row["outcome"], "top3": top}


def examples(cases: list[dict], rows_by_ranker: dict[str, list[dict]], per_kind: int = 4) -> dict:
    by_case = {c["case_id"]: c for c in cases}
    out = {}
    for name, rows in rows_by_ranker.items():
        unsafe = [r for r in rows if r["outcome"] == "unsafe"][:per_kind + 2]
        missed = [r for r in rows if r["label"] == "match" and r["hit1"] == 0][:per_kind]
        out[name] = {"unsafe": [describe(by_case[r["case_id"]], r) for r in unsafe],
                     "wrong_top1_on_match": [describe(by_case[r["case_id"]], r) for r in missed]}
    return out


def floor_ablation(systems: dict, rankers: dict, rows_by: dict, test: list[dict]) -> dict:
    """Same systems with the business act floor removed: what the floor buys in safety and costs in automation."""
    out = {}
    for name, (rk, decider) in systems.items():
        floor = getattr(getattr(decider, "policy", None), "act_floor", None)
        if not floor:
            continue
        without = run_system(rankers[rk], decider.with_floor(0.0), test)
        with_rows = rows_by[name]
        s_with, s_without = summarize(with_rows, with_ci=False), summarize(without, with_ci=False)
        out[name] = {
            "act_floor": floor, "val_t_act": decider.policy.t_act,
            "with_floor": {k: s_with[k] for k in ("unsafe", "correct_decision_rate", "safe_automated_resolution_rate",
                                                  "automation_attempted_share")},
            "without_floor": {k: s_without[k] for k in ("unsafe", "correct_decision_rate",
                                                        "safe_automated_resolution_rate", "automation_attempted_share")},
            "with_minus_without": {"unsafe_rate": paired_difference(without, with_rows, unsafe_rate),
                                   "safe_automated_resolution_rate": paired_difference(without, with_rows, safe_auto_rate),
                                   "correct_decision_rate": paired_difference(without, with_rows, correct_rate)},
        }
    return out


def parse_diagnostics(cases: list[dict]) -> dict:
    """How often the shared parser recovers the amount and date cues the description was built from.

    Uses the stored hints, which only the evaluation may read. Explains ranker failures on held-out phrasing.
    """
    from datetime import date as _date

    from ml.features.parse import parse_description

    out: dict = {}
    for c in cases:
        key = f"{c['language']}/{'held_out' if c['family_heldout'] else 'seen'}"
        p = parse_description(c["description"], _date.fromisoformat(c["report_date"]))
        h = c["hints"]
        slot = out.setdefault(key, {"amount_given": 0, "amount_read": 0, "date_given": 0, "date_read": 0})
        if "amount" in h:
            slot["amount_given"] += 1
            slot["amount_read"] += bool(p.amount and abs(p.amount / h["amount"]["claimed"] - 1) < 0.01)
        if "date" in h:
            slot["date_given"] += 1
            slot["date_read"] += bool(p.date_lo and p.date_lo.isoformat() == h["date"]["lo"]
                                      and p.date_hi.isoformat() == h["date"]["hi"])
    return dict(sorted(out.items()))


def stratified_subset(cases: list[dict], n: int, seed: int = 5) -> list[dict]:
    """Spanish sources sampled per label, with their Portuguese twins, for the costlier LLM runs."""
    rng = random.Random(seed)
    es = [c for c in cases if c["language"] == "es"]
    picked = set()
    for lab in ("match", "ambiguous", "no_match"):
        pool = sorted(c["case_id"] for c in es if c["label"] == lab)
        share = sum(c["label"] == lab for c in es) / len(es)
        picked.update(rng.sample(pool, min(len(pool), round(n * share))))
    return [c for c in cases if group_of(c) in picked]


def run_llm(train: list[dict], val: list[dict], test: list[dict]) -> dict:
    from ml.rankers.llm import LLMRanker, availability

    ok, reason = availability()
    if not ok:
        return {"status": "not run", "reason": reason}
    ranker = LLMRanker()
    guarded = _Guarded(ranker)
    tr, va, te = (stratified_subset(s, LLM_SUBSET[k]) for s, k in ((train, "train"), (val, "val"), (test, "test")))
    rank = lambda cases: [record(c, guarded.rank(ranker_input(c), c["candidates"])) for c in cases]  # noqa: E731
    decider, info = CalibratedDecider.fit(rank(tr), rank(va), MAX_UNSAFE_RATE)
    runs = []
    for k in range(LLM_RUNS):
        start_cost = ranker.cost_usd() or 0.0
        rows = run_system(guarded, decider, te)
        spent = (ranker.cost_usd() or 0.0) - start_cost
        s = summarize(rows, n_boot=500)
        s["cost_usd_total"] = round(spent, 4)
        s["cost_usd_per_case"] = round(spent / len(rows), 5)
        n_ok = sum(r["decision"] == "act" and r["outcome"] == "correct" for r in rows)
        s["cost_usd_per_safe_automated_resolution"] = round(spent / n_ok, 5) if n_ok else "not defined"
        runs.append({"run": k + 1, "summary": s, "rows": rows})
    return {"status": "run", "model": ranker.model, "prompt_version": ranker.prompt_version,
            "subset_sizes": {"train": len(tr), "val": len(va), "test": len(te)}, "decider": decider.params(),
            "val_info": info, "runs": runs}


class _Guarded:
    """An API failure becomes an empty ranking, which the decision layer turns into abstain (handoff)."""

    def __init__(self, ranker):
        self.ranker, self.name = ranker, ranker.name

    def rank(self, features, candidates):
        try:
            return self.ranker.rank(features, candidates)
        except Exception as exc:
            print(f"llm error: {type(exc).__name__}")
            return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--split", choices=("test", FRESH), default="test",
                    help=f"{FRESH} is evaluated once: the run refuses to overwrite an existing {RESULTS[FRESH]}")
    args = ap.parse_args()
    out = REPORTS_DIR / RESULTS[args.split]
    if args.split == FRESH:
        check_fresh_frozen(out)
    test = load_cases(args.split)
    version = data_version()
    fitted = json.loads((REPORTS_DIR / "fitted.json").read_text(encoding="utf-8"))
    if fitted["data_version"] != version:
        raise SystemExit("fitted artifacts were trained on another data version; run ml.train first")
    with (MODELS_DIR / "systems.pkl").open("rb") as fh:  # local artifact written by ml/train.py
        systems = pickle.load(fh)
    rankers = {"rules": RuleRanker(), "learned": LearnedRanker.load(MODELS_DIR / "learned.pkl")}
    rows_by: dict[str, list[dict]] = {}
    results: dict = {"generated_by": "ml/evaluate.py", "data_version": version, "split": args.split,
                     "proposed_system": PROPOSED, "systems": {}}
    if args.split == FRESH:
        results["fresh_version"] = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))[
            FRESH]["fresh_version"]
    for name, (rk, decider) in systems.items():
        rows = run_system(rankers[rk], decider, test)
        rows_by[name] = rows
        results["systems"][name] = {"ranker": rankers[rk].name, "decider": decider.name,
                                    "decider_params": decider.params(), "summary": summarize(rows),
                                    "breakdowns": breakdowns(rows), "selective_curve": curve(rows)}
    results["act_floor_ablation"] = floor_ablation(systems, rankers, rows_by, test)
    results["country_disparity"] = country_disparity(rows_by["rules_tuned"], rows_by[PROPOSED])
    results["paired_differences"] = {}
    for base in ("rules_fixed", "rules_tuned"):
        diffs = {}
        for metric, fn in (("top1_accuracy", top1), ("mrr", mrr), ("correct_decision_rate", correct_rate),
                           ("unsafe_rate", unsafe_rate)):
            diffs[metric] = paired_difference(rows_by[base], rows_by[PROPOSED], fn)
            for fam in ("seen", "held_out"):
                a = [r for r in rows_by[base] if r["family_split"] == fam]
                b = [r for r in rows_by[PROPOSED] if r["family_split"] == fam]
                diffs[f"{metric}_{fam}"] = paired_difference(a, b, fn)
        results["paired_differences"][f"{PROPOSED}_minus_{base}"] = diffs
        diffs["safe_automated_resolution_rate"] = paired_difference(rows_by[base], rows_by[PROPOSED], safe_auto_rate)
    results["paired_by_group"] = {key: paired_by_group(rows_by["rules_tuned"], rows_by[PROPOSED], key)
                                  for key in ("language", "country", "family_split", "family")}
    if args.split == FRESH:
        results["llm"] = {"status": "not run", "reason": "test_fresh evaluates the already-trained systems only"}
    else:
        results["llm"] = {"status": "not run", "reason": "--no-llm"} if args.no_llm else run_llm(
            load_cases("train"), load_cases("val"), test)
    results["examples"] = examples(test, rows_by)
    results["parser_cue_recovery"] = parse_diagnostics(test)
    results["cost_assumptions"] = ("rules and learned rankers run on local CPU; no per-call charge is metered, so "
                                   "their cost per case is reported as 0 USD of API spend. LLM cost uses list "
                                   "prices in ml/rankers/llm.py PRICING times the tokens reported by the API.")
    for run in results["llm"].get("runs", []):
        run.pop("rows", None)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for name, body in results["systems"].items():
        with tracking.run(f"evaluate:{args.split}:{name}", {
                "system": name, "ranker": body["ranker"], "decider": body["decider"], "data_version": version,
                "split": args.split, "prompt_version": "n/a"}):
            tracking.log_metrics({"summary": body["summary"]})
            tracking.log_artifact(out)
    if results["llm"]["status"] == "run":
        with tracking.run("evaluate:llm", {"ranker": "llm", "model": results["llm"]["model"],
                                           "prompt_version": results["llm"]["prompt_version"],
                                           "data_version": version, "runs": LLM_RUNS}):
            for run in results["llm"]["runs"]:
                tracking.log_metrics({f"run{run['run']}": run["summary"]})
    print(json.dumps({n: {k: b["summary"][k]["value"] for k in ("top1_accuracy", "correct_decision_rate",
                                                                 "unsafe_rate", "safe_automated_resolution_rate")}
                      for n, b in results["systems"].items()}, indent=2))


if __name__ == "__main__":
    main()
