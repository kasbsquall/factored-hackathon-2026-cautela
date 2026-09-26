"""Third rung of the ranker ladder: the prompted LLM ranker (rank_v1) on the original test split.

    uv run python -m ml.evaluate_llm [--train 150 --val 150 --runs 3 --parallel 4]

Same protocol as the rules and learned rankers in ml/evaluate.py, through the provider-agnostic port used by
ml/probe_llm.py (masking, schema-checked JSON, token usage), with OpenAI gpt-6-luna and reasoning_effort none:

* the calibrator is fitted on a stratified train subset and the act/abstain thresholds are searched on a
  stratified val subset (Calibrator.fit and search_policy refuse any other split); the business act floor of
  fitted.json applies as for the other systems;
* the full original test split (1,107 cases) is then ranked `--runs` times with the frozen decider; nothing is
  refitted on test, and test_fresh is not touched;
* the rules and learned systems of fitted.json are re-run on the same cases for paired differences.

A provider failure or invalid output is retried up to four times with exponential backoff (a first attempt at 8
parallel calls hit provider rate limits); what still fails is an empty ranking, which the decider turns into abstain
(handoff). Both counts are reported. Every paid call goes through the USD cap of eval/budget.py. Writes
ml/reports/results_llm.json and ml/reports/results_llm.md.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from statistics import mean, stdev

from agent.llm.port import MaskedLLM
from eval import budget as spend
from eval.pools import bucket
from ml.analysis import correct_rate, paired_difference, safe_auto_rate, summarize, top1, unsafe_rate
from ml.data import MODELS_DIR, REPORTS_DIR, data_version, load_cases, ranker_input, record
from ml.decision import CalibratedDecider
from ml.evaluate import case_row, run_system, stratified_subset
from ml.probe_llm import LLMPortRanker
from ml.rankers.learned import LearnedRanker
from ml.rankers.rules import RuleRanker
from ml.train import MAX_UNSAFE_RATE

OUT_JSON = REPORTS_DIR / "results_llm.json"
OUT_MD = REPORTS_DIR / "results_llm.md"
BASELINES = ("rules_tuned", "learned_ranker_disposition")
RETRIES = 4       # provider failures (rate limits seen at 8 parallel calls) are retried with backoff
BACKOFF_S = 2.0
METRICS = {"top1_accuracy": top1, "correct_decision_rate": correct_rate, "unsafe_rate": unsafe_rate,
           "safe_automated_resolution_rate": safe_auto_rate}


def rank_all(ranker: LLMPortRanker, cases: list[dict], parallel: int) -> tuple[dict, dict, dict]:
    """case_id -> ranking ([] on failure), case_id -> latency ms, failure counts by type."""
    errors: Counter = Counter()

    def one(case: dict):
        t0 = time.perf_counter()
        ranked = []
        for attempt in range(RETRIES + 1):
            try:
                ranked = ranker.rank(case)
                break
            except Exception as exc:  # noqa: BLE001 (counted and reported; after the retries, abstain)
                cause = exc.__cause__
                errors[f"{type(exc).__name__}:{type(cause).__name__ if cause else ''}:{getattr(cause, 'code', '')}"
                       + ("" if attempt < RETRIES else ":final")] += 1
                if attempt < RETRIES:
                    time.sleep(BACKOFF_S * 2 ** attempt)
        return case["case_id"], ranked, (time.perf_counter() - t0) * 1000

    with ThreadPoolExecutor(parallel) as pool:
        out = list(pool.map(one, cases))
    return {c: r for c, r, _ in out}, {c: ms for c, _, ms in out}, dict(errors)


def rows_from(cases: list[dict], ranked: dict, ms: dict, decider) -> list[dict]:
    rows = []
    for case in cases:
        conf, d = decider.decide_case(ranker_input(case), case["candidates"], ranked[case["case_id"]])
        rows.append(case_row(case, ranked[case["case_id"]], conf, d, ms[case["case_id"]]))
    return rows


def by_pool_bucket(base_rows: dict[str, list[dict]], llm_rows: list[dict]) -> dict:
    """Headline metrics per candidate pool bucket (eval.pools.bucket: 1, 2-3, 4+) for every rung, and paired
    differences inside each bucket. The scenarios have no pools under 3 (min_pool=3 in ml/scenarios/build.py)."""
    systems = dict(base_rows) | {"llm_ranker_calibrated_run1": llm_rows}
    out: dict = {}
    for b in sorted({bucket(r["pool_size"]) for r in llm_rows}):
        sub = {n: [r for r in rows if bucket(r["pool_size"]) == b] for n, rows in systems.items()}
        rules, learned = sub["rules_tuned"], sub["learned_ranker_disposition"]
        out[b] = {"cases": len(rules),
                  "systems": {n: {k: round(fn(rows), 4) for k, fn in METRICS.items()}
                              | {"unsafe_count": sum(r["outcome"] == "unsafe" for r in rows)}
                              for n, rows in sub.items()},
                  "paired": {"learned_minus_rules": {k: paired_difference(rules, learned, fn) for k, fn in METRICS.items()},
                             "llm_minus_learned": {k: paired_difference(learned, sub["llm_ranker_calibrated_run1"], fn)
                                                   for k, fn in METRICS.items()}}}
    return out


def run_variance(runs: list[list[dict]]) -> dict:
    per = {k: [fn(rows) for rows in runs] for k, fn in METRICS.items()}
    decisions = [{r["case_id"]: (r["decision"], tuple(r["top_k"][:1])) for r in rows} for rows in runs]
    same = sum(len({d[c] for d in decisions}) == 1 for c in decisions[0])
    return {"per_run": {k: [round(v, 4) for v in vals] for k, vals in per.items()},
            "sd": {k: round(stdev(vals), 4) if len(vals) > 1 else None for k, vals in per.items()},
            "mean": {k: round(mean(vals), 4) for k, vals in per.items()},
            "same_decision_all_runs": {"count": same, "denominator": len(decisions[0])}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train", type=int, default=150, help="Spanish source cases in the train subset")
    ap.add_argument("--val", type=int, default=150, help="Spanish source cases in the val subset")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    fitted = json.loads((REPORTS_DIR / "fitted.json").read_text(encoding="utf-8"))
    if fitted["data_version"] != data_version():
        raise SystemExit("fitted.json belongs to another data version")
    train = stratified_subset(load_cases("train"), args.train)
    val = stratified_subset(load_cases("val"), args.val)
    test = load_cases("test")
    need = 0.00015 * (len(train) + len(val) + args.runs * len(test))
    ledger = spend.ledger()
    if spend.remaining(ledger) < need:
        raise SystemExit(f"estimated {need:.2f} USD exceeds the remaining cap {spend.remaining(ledger):.2f} USD")
    refused_before = ledger.status()["refused_calls"]
    llm = MaskedLLM(spend.paid_adapter(ledger), max_tokens=600)
    ranker = LLMPortRanker(llm)
    usd0 = ledger.status()["usd_estimated"]
    fit_ranked = {}
    fit_errors: Counter = Counter()
    for cases in (train, val):
        ranked, _, errors = rank_all(ranker, cases, args.parallel)
        fit_ranked.update(ranked)
        fit_errors.update(errors)
    decider, info = CalibratedDecider.fit([record(c, fit_ranked[c["case_id"]]) for c in train],
                                          [record(c, fit_ranked[c["case_id"]]) for c in val], MAX_UNSAFE_RATE)
    decider = decider.with_floor(float(fitted.get("act_floor", 0.6)))
    usd_fit = ledger.status()["usd_estimated"] - usd0
    runs, run_meta = [], []
    for k in range(args.runs):
        usd_before, t0 = ledger.status()["usd_estimated"], time.perf_counter()
        ranked, ms, errors = rank_all(ranker, test, args.parallel)
        rows = rows_from(test, ranked, ms, decider)
        spent = ledger.status()["usd_estimated"] - usd_before
        ok = sum(r["decision"] == "act" and r["outcome"] == "correct" for r in rows)
        runs.append(rows)
        run_meta.append({"run": k + 1, "wall_s": round(time.perf_counter() - t0, 1), "provider_errors": errors,
                         "cost_usd": round(spent, 5), "cost_usd_per_case": round(spent / len(rows), 7),
                         "cost_usd_per_safe_automated_resolution": round(spent / ok, 7) if ok else "not defined"})
    with (MODELS_DIR / "systems.pkl").open("rb") as fh:  # local artifact written by ml/train.py
        systems = pickle.load(fh)
    base_rankers = {"rules": RuleRanker(), "learned": LearnedRanker.load(MODELS_DIR / "learned.pkl")}
    base_rows = {name: run_system(base_rankers[systems[name][0]], systems[name][1], test) for name in BASELINES}
    first = runs[0]
    status = ledger.status()
    results = {
        "generated_by": "ml/evaluate_llm.py", "data_version": data_version(), "split": "test",
        "system": "llm_ranker_calibrated", "provider": "openai", "model": "gpt-6-luna", "reasoning_effort": "none",
        "prompt_version": ranker.prompt_version, "port_prompt_version": "llm-port (agent/llm/port.py)",
        "fit": {"train_cases": len(train), "val_cases": len(val), "provider_errors": dict(fit_errors),
                "decider": decider.params(), "val_info": info, "cost_usd": round(usd_fit, 5)},
        "run1_summary": summarize(first),
        "runs": run_meta, "variance": run_variance(runs),
        "baselines_same_cases": {n: {k: round(fn(rows), 4) for k, fn in METRICS.items()}
                                 | {"unsafe_count": sum(r["outcome"] == "unsafe" for r in rows)}
                                 for n, rows in base_rows.items()},
        "paired_run1": {f"llm_minus_{n}": {k: paired_difference(rows, first, fn) for k, fn in METRICS.items()}
                        for n, rows in base_rows.items()},
        "by_pool_bucket": by_pool_bucket(base_rows, first),
        "by_language_run1": {lang: {k: round(fn([r for r in first if r["language"] == lang]), 4)
                                    for k, fn in METRICS.items()} for lang in ("es", "pt")},
        "spend": {"this_script_usd": round(status["usd_estimated"] - usd0, 5), "ledger": status,
                  "refused_calls": status["refused_calls"] - refused_before},
        "cost_assumptions": "USD = provider-reported tokens x list price in agent/llm/prices.yaml (gpt-6-luna "
                            "0.10 input / 0.50 output per million tokens, read 2026-09-25); not an invoice.",
    }
    OUT_JSON.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(markdown(results), encoding="utf-8", newline="\n")
    print(json.dumps({"run1": {k: results["run1_summary"][k] for k in METRICS}, "variance": results["variance"],
                      "spend": results["spend"]["this_script_usd"]}, indent=1))


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(entry: dict) -> str:
    ci = entry.get("ci95")
    return "" if not ci else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def markdown(r: dict) -> str:
    s, v, base = r["run1_summary"], r["variance"], r["baselines_same_cases"]
    lines = [
        "# Ranker ladder, third rung: prompted LLM ranker on the original test split",
        "",
        f"Generated by `ml/evaluate_llm.py` from `results_llm.json`; data version `{r['data_version']}`. Offline "
        "evaluation on generated scenarios over organizer transactions, not production results.",
        "",
        f"* Model: {r['provider']} `{r['model']}`, reasoning_effort {r['reasoning_effort']}, prompt "
        f"`{r['prompt_version']}` through the provider-agnostic port (masked input, JSON schema check).",
        f"* Calibrator fitted on {r['fit']['train_cases']} train cases, thresholds on {r['fit']['val_cases']} val cases "
        "(stratified subsets, Spanish sources with their Portuguese twins), act floor as fitted.json. Nothing was "
        "refitted on test; test_fresh was not used.",
        f"* Test: all {s['n_cases']} cases of the original test split, ranked {len(r['runs'])} times with the frozen "
        "decider. The original test split was used for error analysis of the other rankers, so all three rungs are "
        "optimistic on it in the same way; the LLM rung never saw it before this run.",
        "",
        "| system (test, same cases) | top-1 on match | correct decisions | unsafe | safe automated resolution |",
        "|---|---|---|---|---|",
    ]
    for name in BASELINES:
        b = base[name]
        lines.append(f"| `{name}` | {_pct(b['top1_accuracy'])} | {_pct(b['correct_decision_rate'])} | "
                     f"{b['unsafe_count']} / {s['n_cases']} | {_pct(b['safe_automated_resolution_rate'])} |")
    lines.append(f"| `llm_ranker_calibrated` (run 1) | {_pct(s['top1_accuracy']['value'])} "
                 f"{_ci(s['top1_accuracy'])} | {_pct(s['correct_decision_rate']['value'])} "
                 f"{_ci(s['correct_decision_rate'])} | {s['unsafe']['count']} / {s['n_cases']} | "
                 f"{_pct(s['safe_automated_resolution_rate']['value'])} |")
    lines += ["", "Paired differences, LLM run 1 minus baseline, same cases (points, 95% group bootstrap):", "",
              "| comparison | metric | difference | 95% CI |", "|---|---|---|---|"]
    for comp, metrics in r["paired_run1"].items():
        for m, d in metrics.items():
            lo, hi = d["ci95"]
            lines.append(f"| {comp} | {m} | {100 * d['diff']:+.1f} | [{100 * lo:+.1f}, {100 * hi:+.1f}] |")
    lines += ["", "Run-to-run variability (full test split each run):", "",
              "| metric | per run | mean | sd |", "|---|---|---|---|"]
    for m, vals in v["per_run"].items():
        lines.append(f"| {m} | {', '.join(_pct(x) for x in vals)} | {_pct(v['mean'][m])} | "
                     f"{'n/a' if v['sd'][m] is None else f'{100 * v['sd'][m]:.2f} pts'} |")
    same = v["same_decision_all_runs"]
    lines += ["", f"Same decision and same top charge in every run: {same['count']} of {same['denominator']} cases.",
              "", "| run | wall s | provider errors | USD | USD per case | USD per safe automated resolution |",
              "|---|---|---|---|---|---|"]
    for m in r["runs"]:
        lines.append(f"| {m['run']} | {m['wall_s']} | {m['provider_errors'] or 0} | {m['cost_usd']} | "
                     f"{m['cost_usd_per_case']} | {m['cost_usd_per_safe_automated_resolution']} |")
    lines += ["", f"Fitting spend {r['fit']['cost_usd']} USD; total for this script {r['spend']['this_script_usd']} USD "
              f"({r['cost_assumptions']}). Calls refused by the cap: {r['spend']['refused_calls']}.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
