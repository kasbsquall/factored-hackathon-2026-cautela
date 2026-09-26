"""Repeat the security categories (injection, unauthorized) with the LLM on and report the run-to-run spread.

    uv run python -m eval.repeat --runs 2

Run 1 is the main llm run already in eval/results.json; this adds `--runs` more runs of every injection and
unauthorized conversation (118) under the same USD cap and ledger, and stores per-run counts of unsafe and correct
conversations in results["configs"]["llm"]["security_repeats"].
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from eval import budget as spend
from eval.judge import load_suite
from eval.paths import RESULTS_PATH, RUNS_DIR, SUITE_PATH
from eval.run import check_frozen, run_config

CATEGORIES = ("injection", "unauthorized")
COST_PER_CONV_ESTIMATE = 0.0006  # USD, same pre-flight bound as eval/run.py


def security_repeats(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per run and category: conversations, correct, unsafe (and which), plus how many outcomes repeat."""
    per_run = []
    for i, run in enumerate(runs):
        rows = [r for r in run["rows"] if r["category"] in CATEGORIES]
        entry: dict[str, Any] = {"run": i + 1, "run_id": run["run_id"], "conversations": len(rows)}
        for cat in CATEGORIES:
            rs = [r for r in rows if r["category"] == cat]
            entry[cat] = {"conversations": len(rs), "correct": sum(r["correct"] for r in rs),
                          "unsafe": sum(bool(r["unsafe"]) for r in rs),
                          "unsafe_conversations": sorted(r["conv_id"] for r in rs if r["unsafe"])}
        entry["cost_usd"] = round(sum(r["llm_cost_usd"] or 0 for r in rows), 6)
        per_run.append(entry)
    outcome = [{r["conv_id"]: (r["outcome"], r["handoff_code"]) for r in run["rows"] if r["category"] in CATEGORIES}
               for run in runs]
    common = set.intersection(*(set(o) for o in outcome)) if outcome else set()
    same = sum(len({o[c] for o in outcome}) == 1 for c in common)
    return {"categories": list(CATEGORIES), "runs": per_run, "same_outcome_all_runs": {"k": same, "n": len(common)}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=2, help="extra runs besides the main llm run")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    check_frozen()
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    body = results["configs"].get("llm")
    if body is None:
        raise SystemExit("run the llm configuration first (make eval-llm)")
    suite = [s for s in load_suite(SUITE_PATH) if s["category"] in CATEGORIES]
    ledger = spend.ledger()
    need = COST_PER_CONV_ESTIMATE * len(suite) * args.runs
    if spend.remaining(ledger) < need:
        raise SystemExit(f"estimated {need:.2f} USD exceeds the remaining cap {spend.remaining(ledger):.2f} USD")
    refused_before = ledger.status()["refused_calls"]
    main_rows = [json.loads(x) for x in (RUNS_DIR / body["run_id"] / "rows.jsonl").read_text(encoding="utf-8")
                 .splitlines() if x]
    runs = [{"run_id": body["run_id"], "rows": main_rows}]
    for k in range(args.runs):
        runs.append(run_config("llm", suite, "compliant", args.workers, ledger, tag=f"-sec{k + 2}"))
    out = security_repeats(runs)
    out["refused_calls"] = ledger.status()["refused_calls"] - refused_before
    out["valid"] = out["refused_calls"] == 0
    body["security_repeats"] = out
    results["spend"] = ledger.status() | {"cap_usd": spend.CAP_USD, "ledger": "data/eval/llm_spend.json"}
    RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({r["run"]: {c: (r[c]["correct"], r[c]["unsafe"]) for c in CATEGORIES} for r in out["runs"]}))


if __name__ == "__main__":
    main()
