"""Repeat the high-risk categories of the error-analysis suite and measure how much the outcome moves between runs.

    uv run python -m eval.high_risk_repeats          # or `make eval-repeats`

Runs every conversation of the categories injection, unauthorized, adversarial, identity and human_request of the
frozen suite (eval/heldout/suite.jsonl, the suite used for error analysis) three times for each of the rules, learned
and llm configurations, at the current commit, with the compliant simulated customer. For each category and
configuration it reports the share of conversations whose final outcome and handoff reason are identical in every
run, the unsafe and correct conversations of each run, and every conversation whose outcome changed.

rules and learned make no model call and are expected to agree in 100% of conversations: that is a check of the
harness, recorded as `deterministic_check`. For llm the variation comes from the model (gpt-6-luna, reasoning effort
none, through eval/budget.py). Its paid calls are counted on their own ledger, data/eval/llm_spend_repeats.json
(git-ignored), with a hard cap of USD 0.50 for all repeats together; nothing starts when the pre-flight estimate
exceeds what is left, and the runs stop at the first call the cap refuses.

These repeats measure variability only. Nothing is chosen or tuned from them, and the separately frozen second suite
(eval/fresh, test_fresh) is not read.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from eval import budget as spend
from eval.judge import load_suite
from eval.metrics import latency, rate
from eval.paths import DATA_DIR, EVAL_DIR, SUITE_PATH
from eval.run import COST_PER_CONV_ESTIMATE, CONFIGS, _rel, check_frozen, code_state, run_config

CATEGORIES = ("injection", "unauthorized", "adversarial", "identity", "human_request")
CONFIG_NAMES = ("rules", "learned", "llm")
RUNS = 3
CAP_USD = 0.50
LEDGER_PATH = DATA_DIR / "llm_spend_repeats.json"
OUT_PATH = EVAL_DIR / "results_repeats.json"


def outcome_of(row: dict) -> str:
    return row["outcome"] + (":" + row["handoff_code"] if row["handoff_code"] else "")


def agreement(runs: list[list[dict]]) -> dict[str, Any]:
    """Per category (and over all of them): identical outcome and handoff reason in every run, unsafe and correct per
    run, and the conversations whose outcome changed with what each run ended in."""
    by_run = [{r["conv_id"]: r for r in rows} for rows in runs]
    ids = sorted(set.intersection(*(set(b) for b in by_run))) if by_run else []
    changed = []
    for c in ids:
        outcomes = [(b[c]["outcome"], b[c]["handoff_code"]) for b in by_run]
        if len(set(outcomes)) > 1:
            first = by_run[0][c]
            changed.append({"conv_id": c, "category": first["category"], "subcategory": first["subcategory"],
                            "outcomes": [outcome_of(b[c]) for b in by_run],
                            "correct": [bool(b[c]["correct"]) for b in by_run],
                            "unsafe": [list(b[c]["unsafe"]) for b in by_run]})
    moved = {x["conv_id"] for x in changed}
    out: dict[str, Any] = {}
    for cat in (*CATEGORIES, "all"):
        cids = [c for c in ids if cat == "all" or by_run[0][c]["category"] == cat]
        if not cids:
            continue
        out[cat] = {"conversations": len(cids),
                    "same_outcome_all_runs": rate(sum(c not in moved for c in cids), len(cids)),
                    "correct_per_run": [sum(bool(b[c]["correct"]) for c in cids) for b in by_run],
                    "unsafe_per_run": [sum(bool(b[c]["unsafe"]) for c in cids) for b in by_run],
                    "unsafe_conversations_per_run": [sorted(c for c in cids if b[c]["unsafe"]) for b in by_run]}
    return {"by_category": out, "changed": changed}


def run_entry(run: dict, refused: int) -> dict[str, Any]:
    rows = run["rows"]
    lat = latency(rows)
    costs = [r["llm_cost_usd"] for r in rows]
    return {"run_id": run["run_id"], "conversations": len(rows), "wall_s": run["wall_s"], "workers": run["workers"],
            "harness_errors": sum(bool(r["harness_error"]) for r in rows),
            "llm_calls": sum(r["llm_calls"] for r in rows), "llm_failed_calls": sum(r["llm_failed"] for r in rows),
            "llm_input_tokens": sum(r["llm_input_tokens"] for r in rows),
            "llm_output_tokens": sum(r["llm_output_tokens"] for r in rows),
            "llm_cost_usd": round(sum(c for c in costs if c is not None), 6),
            "conversations_with_unknown_cost": sum(c is None for c in costs), "calls_refused_by_cap": refused,
            "per_call_wall_ms": lat["per_call_wall_ms"], "per_conversation_wall_ms": lat["per_conversation_wall_ms"]}


def preflight(remaining_usd: float, conversations: int, runs: int) -> float:
    """Estimated USD of the paid runs; SystemExit before anything runs when it exceeds what the cap leaves."""
    need = round(COST_PER_CONV_ESTIMATE * conversations * runs, 6)
    if remaining_usd < need:
        raise SystemExit(f"estimated {need:.4f} USD exceeds the remaining cap {remaining_usd:.4f} USD; nothing run")
    return need


def repeat_config(name: str, suite: list[dict], runs: int, workers: int, ledger) -> dict[str, Any]:
    paid = CONFIGS[name]["llm"]
    body: dict[str, Any] = {"description": CONFIGS[name]["description"], "code": code_state(output=OUT_PATH),
                            "runs": []}
    rows_per_run = []
    for k in range(runs):
        refused_before = ledger.status()["refused_calls"] if paid else 0
        run = run_config(name, suite, "compliant", workers if paid else 1, ledger if paid else None,
                         tag=f"-hr{k + 1}")
        refused = (ledger.status()["refused_calls"] - refused_before) if paid else 0
        body["runs"].append(run_entry(run, refused))
        body.setdefault("disposition_model", run["disposition_model"])
        body.setdefault("disposition_source", run["disposition_source"])
        rows_per_run.append(run["rows"])
        if refused:
            body["stopped"] = f"run {k + 1}: {refused} calls refused by the USD cap; later runs not started"
            break
    body["complete"] = len(rows_per_run) == runs and "stopped" not in body
    body |= agreement(rows_per_run)
    if not paid:
        same = body["by_category"]["all"]["same_outcome_all_runs"]
        body["deterministic_check"] = {"expected": "identical outcome and handoff reason in every run",
                                       "passed": body["complete"] and same["k"] == same["n"]}
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default=",".join(CONFIG_NAMES))
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--workers", type=int, default=6, help="threads for the llm configuration")
    ap.add_argument("--cap-usd", type=float, default=CAP_USD, help="hard USD cap for every paid repeat together")
    args = ap.parse_args()
    names = [c for c in args.configs.split(",") if c]
    if unknown := [c for c in names if c not in CONFIG_NAMES]:
        raise SystemExit(f"unknown configurations: {unknown}")
    manifest = check_frozen()
    suite = [s for s in load_suite(SUITE_PATH) if s["category"] in CATEGORIES]
    ledger = spend.ledger(args.cap_usd, LEDGER_PATH)
    estimate = None
    if any(CONFIGS[n]["llm"] for n in names):
        estimate = preflight(spend.remaining(ledger), len(suite), args.runs)
    results = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
    results.update({"generated_by": "eval/high_risk_repeats.py", "suite": "eval/heldout/suite.jsonl (error analysis)",
                    "suite_sha256": manifest["suite_sha256"],
                    "warehouse_slice_content_hash": manifest["warehouse_slice_content_hash"],
                    "categories": list(CATEGORIES), "runs_per_config": args.runs, "customer": "compliant",
                    "conversations": len(suite),
                    "conversations_by_category": {c: sum(s["category"] == c for s in suite) for c in CATEGORIES}})
    results.setdefault("configs", {})
    for name in names:
        body = repeat_config(name, suite, args.runs, args.workers, ledger)
        if CONFIGS[name]["llm"]:
            body["preflight_estimate_usd"] = estimate
        results["configs"][name] = body
        all_ = body["by_category"]["all"]
        print(json.dumps({name: {"same_outcome_all_runs": all_["same_outcome_all_runs"],
                                 "unsafe_per_run": all_["unsafe_per_run"], "changed": len(body["changed"])}}))
        if CONFIGS[name]["llm"] or "spend" not in results:
            results["spend"] = ledger.status() | {"cap_usd": args.cap_usd, "ledger": _rel(LEDGER_PATH)}
        # written after every configuration, so a stopped paid run keeps the deterministic results
        OUT_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {_rel(OUT_PATH)}")
    failed = [n for n, b in results["configs"].items() if not b.get("deterministic_check", {"passed": True})["passed"]]
    if failed:
        raise SystemExit(f"deterministic configurations did not repeat exactly: {failed}")


if __name__ == "__main__":
    main()
