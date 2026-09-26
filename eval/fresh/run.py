"""Run eval_fresh once per configuration and write eval/fresh/results.json.

    uv run python -m eval.fresh.run --configs rules
    uv run python -m eval.fresh.run --configs learned --attentive
    uv run python -m eval.fresh.run --configs llm --variance-runs 2      # paid, under the eval/budget.py cap

Same configurations, harness, simulated customer, judge and aggregates as eval/run.py, on the eval_fresh suite and
the warehouse slice of the test_fresh customers. Before anything runs, the suite, gold file, policy version and
slice are checked against eval/fresh/manifest.json, and every requested configuration is checked against the
run-once markers in eval/fresh/ran/ (eval/fresh/guard.py): a configuration that already ran is refused, and nothing
runs. After each configuration the results are written and its marker is created; commit both right away:

    git add eval/fresh/results.json eval/fresh/ran/<config>.json

--rerun-not-for-reporting skips the markers for debugging. It is never used for reporting: results go to
data/eval/fresh_unreported/ (git-ignored), and eval/fresh/results.json and the markers are not touched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval import budget as spend
from eval.fresh import guard
from eval.judge import RUBRIC, UNSAFE_TYPES, load_suite
from eval.metrics import summarize
from eval.paths import FRESH_SLICE_PATH, FRESH_SUITE_PATH, RUNS_DIR
from eval.run import CONFIGS, COST_PER_CONV_ESTIMATE, aggregate, compare, run_config, variance

TAG = "-fresh"


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", required=True, help="comma-separated: rules, learned, llm")
    ap.add_argument("--attentive", action="store_true", help="also run the attentive customer (deterministic configs)")
    ap.add_argument("--variance-runs", type=int, default=0, help="extra LLM runs on the variance subset")
    ap.add_argument("--workers", type=int, default=6, help="threads for the LLM configuration")
    ap.add_argument("--limit", type=int, default=0,
                    help=f"debug: first N conversations; requires {guard.OVERRIDE_FLAG}")
    ap.add_argument(guard.OVERRIDE_FLAG, dest="override", action="store_true",
                    help="debug only, never used for reporting: ignore the run-once markers and write results to "
                         "data/eval/fresh_unreported/ instead of eval/fresh/results.json")
    args = ap.parse_args(argv)
    args.config_list = [c for c in args.configs.split(",") if c]
    unknown = [c for c in args.config_list if c not in CONFIGS]
    if unknown or not args.config_list:
        ap.error(f"unknown configurations {unknown}; choose from {sorted(CONFIGS)}")
    return args


def _write(path: Path, results: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    manifest = guard.check_frozen()
    guard.check_run_once(args.config_list, args.override, args.limit)
    suite = load_suite(FRESH_SUITE_PATH)
    if args.limit:
        suite = suite[:args.limit]
    out = guard.results_path_for(args.override)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    results.update({"generated_by": "eval/fresh/run.py", "suite": "eval_fresh",
                    "suite_version": manifest["suite_version"], "suite_sha256": manifest["suite_sha256"],
                    "gold_sha256": manifest["gold_sha256"],
                    "warehouse_slice_content_hash": manifest["warehouse_slice_content_hash"],
                    "suite_conversations": len(suite), "reportable": not args.override})
    results.setdefault("configs", {})
    ledger = spend.ledger()
    runs: dict[str, dict] = {}
    for name in args.config_list:
        llm = CONFIGS[name]["llm"]
        workers = args.workers if llm else 1
        if llm:
            need = COST_PER_CONV_ESTIMATE * len(suite) * (1 + 0.25 * args.variance_runs)
            if spend.remaining(ledger) < need:
                raise SystemExit(f"estimated spend {need:.2f} USD exceeds the remaining cap "
                                 f"{spend.remaining(ledger):.2f} USD; nothing run for {name}")
        refused_before = ledger.status()["refused_calls"]
        run = run_config(name, suite, "compliant", workers, ledger if llm else None, tag=TAG,
                         slice_path=FRESH_SLICE_PATH)
        runs[name] = run
        body = {"description": CONFIGS[name]["description"], **aggregate(run)}
        if llm:
            body["budget_after_run"] = ledger.status()
            refused = ledger.status()["refused_calls"] - refused_before
            body["valid"] = refused == 0
            if refused:
                body["invalid_reason"] = f"{refused} calls refused by the USD cap: the run fell back to the parser"
            extra = [run]
            sub = [s for s in suite if s["variance_subset"]]
            for k in range(args.variance_runs):
                extra.append(run_config(name, sub, "compliant", workers, ledger, tag=f"{TAG}-var{k + 2}",
                                        slice_path=FRESH_SLICE_PATH))
            if args.variance_runs:
                body["variance"] = variance(extra)
                body["budget_after_variance"] = ledger.status()
        if args.attentive and not llm:
            att = run_config(name, suite, "attentive", workers, tag=TAG, slice_path=FRESH_SLICE_PATH)
            body["attentive_customer"] = {"run_id": att["run_id"],
                                          "summary": summarize(att["rows"], UNSAFE_TYPES, RUBRIC),
                                          "by_category": aggregate(att)["by_category"]}
        results["configs"][name] = body
        _write(out, results)
        if not args.override:
            marker = guard.write_marker(name, body, manifest, {"attentive": args.attentive,
                                                               "variance_runs": args.variance_runs,
                                                               "workers": workers})
            print(f"{name}: results in {out.name}, marker {marker.name}; commit both now")
        print(json.dumps({name: {k: body["summary"][k] for k in ("correct_outcome", "safe_automated_resolution",
                                                                "unsafe", "containment")}}, indent=1))
    for name, body in results["configs"].items():
        if name not in runs:
            path = RUNS_DIR / body["run_id"] / "rows.jsonl"
            if path.exists():
                runs[name] = {"rows": [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]}
    for a, b in (("rules", "learned"), ("learned", "llm"), ("rules", "llm")):
        if a in runs and b in runs and not args.limit:
            results.setdefault("comparisons", {})[f"{b}_minus_{a}"] = compare(runs[a], runs[b])
    results["spend"] = ledger.status() | {"cap_usd": spend.CAP_USD, "ledger": "data/eval/llm_spend.json"}
    _write(out, results)


if __name__ == "__main__":
    main()
