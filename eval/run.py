"""Run the frozen suite through the whole agent for each configuration and write eval/results.json.

    uv run python -m eval.run                         # rules and learned, LLM off (no network, no cost)
    uv run python -m eval.run --configs llm --variance-runs 2   # gpt-6-luna, capped by eval/budget.py
    uv run python -m eval.run --configs rules,learned,llm       # everything
    uv run python -m eval.run --configs rules,learned,label_rule --out eval/results_after_fix.json
    uv run python -m eval.run --configs llm --out eval/results_after_fix.json --cap-usd 2.00 --ledger PATH

Configurations (same conversations, same simulated customer):
  rules    RuleDisposition (hand-weighted ranker + fixed clarify rule), deterministic parser and templates
  learned  LearnedDisposition (ml/ learned ranker + disposition model), deterministic parser and templates
  llm      LearnedDisposition, MaskedLLM over OpenAI gpt-6-luna (reasoning_effort none) for extraction and replies
  label_rule  baseline for this command only: the scenario label rule on the parsed cues (ml/label_rule.py), LLM off

Every configuration records the git commit it ran on and the paths under agent/, api/, ml/ and eval/ that differed
from that commit when it started, so a results file says which code produced it. Paid runs can use their own ledger
and cap (--ledger, --cap-usd); the cap is enforced per call by eval/budget.py.

The suite is checked against the sha256 in eval/heldout/manifest.json before anything runs. Full transcripts,
audit records and per-conversation rows go to data/eval/runs/<run id>/ (git-ignored); results.json keeps the
aggregates, compact per-conversation outcomes and failure examples with trace ids. Results of previous
configurations in results.json are kept when a run covers only some of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.llm.port import MaskedLLM
from agent.orchestrator import RuleDisposition
from agent.orchestrator.disposition import LabelRuleDisposition, LearnedDisposition
from eval import budget as spend
from eval.harness import customer_ids, run_conversation
from eval.judge import IN_SCOPE, RUBRIC, UNSAFE_TYPES, judge, load_suite, owners
from eval.metrics import breakdown, cost, latency, paired_bootstrap, safe_automated, summarize
from eval.oracle import rules
from eval.paths import MANIFEST_PATH, RESULTS_PATH, ROOT, RUNS_DIR, SLICE_PATH, SPEND_LEDGER, SUITE_PATH
from eval.pools import pool_bucket_of

CONFIGS = {
    "rules": {"disposition": "rules", "llm": False,
              "description": "rules baseline disposition (ml rules ranker + fixed clarify rule), LLM off"},
    "learned": {"disposition": "learned", "llm": False,
                "description": "learned disposition (ml learned ranker + disposition model), LLM off"},
    "llm": {"disposition": "learned", "llm": True,
            "description": "learned disposition, LLM on: openai gpt-6-luna, reasoning_effort none, "
                           "extraction and replies through agent/llm (masked, schema-checked, grounded)"},
}
# Baselines that only this module's command line runs; CONFIGS stays the protocol of the frozen suites.
BASELINE_CONFIGS = {
    "label_rule": {"disposition": "label_rule", "llm": False,
                   "description": "label rule on the parsed cues (ml/label_rule.py, nothing fitted), LLM off"},
}
ALL_CONFIGS = CONFIGS | BASELINE_CONFIGS
COST_PER_CONV_ESTIMATE = 0.0006  # USD, upper estimate for the pre-flight budget check (pilot: see report)


def check_frozen(path=SUITE_PATH) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not path.exists():
        raise SystemExit(f"{path} not found: run `uv run python -m eval.build --verify`")
    sha = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if sha != manifest["suite_sha256"]:
        raise SystemExit("suite.jsonl does not match the sha256 frozen in eval/heldout/manifest.json")
    current = rules()["version"]
    if current != manifest.get("policy_version"):
        raise SystemExit(f"agent/policy/rules.yaml is version {current}, the suite's gold was set under "
                         f"{manifest.get('policy_version')}: rebuild the suite (make eval-suite) before running")
    return manifest


def disposition_for(kind: str):
    if kind == "rules":
        return RuleDisposition()
    return LabelRuleDisposition() if kind == "label_rule" else LearnedDisposition.load()


def code_state(paths: tuple[str, ...] = ("agent", "api", "ml", "eval")) -> dict[str, Any]:
    """The commit a run starts from and the code paths that differ from it (uncommitted changes)."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    dirty = git("status", "--porcelain", "--", *paths).splitlines()
    return {"git_commit": git("rev-parse", "HEAD") or "unknown",
            "uncommitted_paths": sorted(line[3:] for line in dirty if not line.endswith(".md"))}


def row_of(spec: dict, t, verdict) -> dict[str, Any]:
    llm_calls = [c.llm for c in t.calls if c.llm]
    costs = [c.get("cost_usd") for c in llm_calls if c.get("calls")]
    return {
        "conv_id": spec["conv_id"], "category": spec["category"], "subcategory": spec["subcategory"],
        "group": spec["group"], "language": spec["language"], "country": spec["country"],
        "segment": spec["segment"], "family": spec["family"], "tags": spec["tags"],
        "in_scope": spec["category"] in IN_SCOPE, "pool_parity": spec["pool_parity"],
        "variance_subset": spec["variance_subset"], "gold_kind": spec["gold"]["kind"],
        "gold_reasons": spec["gold"]["reasons"], **verdict.as_dict(),
        "trace_ids": [c.trace_id for c in t.calls], "calls": len(t.calls),
        "call_latency_ms": [c.latency_ms for c in t.calls], "call_wall_ms": [c.wall_ms for c in t.calls],
        "llm_calls": sum(c.get("calls", 0) for c in llm_calls),
        "llm_failed": sum(c.get("failed", 0) for c in llm_calls),
        "llm_cost_usd": None if any(c is None for c in costs) else round(sum(costs), 8),
        "llm_input_tokens": sum(c.get("input_tokens", 0) for c in llm_calls),
        "llm_output_tokens": sum(c.get("output_tokens", 0) for c in llm_calls),
        "reply_sources": dict(Counter(c.reply_source for c in t.calls)),
        "reply_notes": dict(Counter(n.split(":")[0] + (":" + n.split(":")[1] if n.count(":") else "")
                                    for c in t.calls for n in c.reply_notes)),
        "understand_sources": dict(Counter(s for c in t.calls for s in c.trail if s.startswith("understand"))),
        "harness_error": t.harness_error,
    }


def run_config(name: str, suite: list[dict], customer: str, workers: int, ledger=None, tag: str = "",
               slice_path=SLICE_PATH) -> dict:
    cfg = ALL_CONFIGS[name]
    disposition = disposition_for(cfg["disposition"])
    llm_factory = None
    if cfg["llm"]:
        adapter = spend.paid_adapter(ledger)

        def llm_factory(audit, adapter=adapter):
            return MaskedLLM(adapter, audit=audit, max_tokens=512)
    run_id = f"{name}-{customer}{tag}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    owner = owners(str(slice_path))
    customer_ids(slice_path)
    started = time.perf_counter()
    with (out_dir / "audit.jsonl").open("w", encoding="utf-8") as audit_sink, \
            (out_dir / "transcripts.jsonl").open("w", encoding="utf-8") as tx_sink:
        def one(spec: dict) -> dict:
            t = run_conversation(spec, disposition, llm_factory, customer, audit_sink, slice_path)
            tx_sink.write(json.dumps({"conv_id": spec["conv_id"], **asdict(t)}, ensure_ascii=False, default=str)
                          + "\n")
            return row_of(spec, t, judge(spec, t, owner))
        if workers > 1:
            with ThreadPoolExecutor(workers) as pool:
                rows = list(pool.map(one, suite))
        else:
            rows = [one(spec) for spec in suite]
    wall = time.perf_counter() - started
    with (out_dir / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    status = ledger.status() if ledger is not None else None
    return {"run_id": run_id, "config": name, "customer": customer, "rows": rows, "wall_s": round(wall, 1),
            "workers": workers, "budget_status": status, "disposition_model": disposition.name,
            "disposition_source": getattr(disposition, "source", None)}


def aggregate(run: dict) -> dict[str, Any]:
    rows = run["rows"]
    summary = summarize(rows, UNSAFE_TYPES, RUBRIC)
    successes = summary["safe_automated_resolution"]["k"]
    by_cat: dict[str, Any] = {}
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == cat]
        by_cat[cat] = {"conversations": len(rs), "correct": sum(r["correct"] for r in rs),
                       "unsafe": sum(bool(r["unsafe"]) for r in rs),
                       "outcomes": dict(Counter(r["outcome"] + (":" + r["handoff_code"] if r["handoff_code"] else "")
                                                for r in rs).most_common())}
    by_sub = {f"{c}/{s}": {"conversations": len(rs), "correct": sum(r["correct"] for r in rs),
                           "unsafe": sum(bool(r["unsafe"]) for r in rs)}
              for (c, s), rs in _group(rows, lambda r: (r["category"], r["subcategory"])).items()}
    return {
        "run_id": run["run_id"], "customer": run["customer"], "wall_s": run["wall_s"], "workers": run["workers"],
        **{k: run[k] for k in ("disposition_model", "disposition_source") if k in run},
        "summary": summary, "latency": latency(rows), "cost": cost(rows, successes),
        "by_category": by_cat, "by_subcategory": dict(sorted(by_sub.items())),
        "by_language": breakdown(rows, "language"), "by_country": breakdown(rows, "country"),
        "by_segment": breakdown(rows, "segment"),
        "by_language_in_scope": breakdown([r for r in rows if r["in_scope"]], "language"),
        "by_pool_bucket_in_scope": breakdown([r | {"pool_bucket": pool_bucket_of(r["group"])} for r in rows
                                              if r["in_scope"] and pool_bucket_of(r["group"])], "pool_bucket"),
        "pool_parity_false": summarize([r for r in rows if not r["pool_parity"]], UNSAFE_TYPES, RUBRIC)["correct_outcome"],
        "harness_errors": sum(bool(r["harness_error"]) for r in rows),
        "llm_fallbacks": {"llm_failed_calls": sum(r["llm_failed"] for r in rows),
                          "reply_sources": dict(sum((Counter(r["reply_sources"]) for r in rows), Counter())),
                          "reply_notes": dict(sum((Counter(r["reply_notes"]) for r in rows), Counter())),
                          "understand_sources": dict(sum((Counter(r["understand_sources"]) for r in rows),
                                                         Counter()))},
        "failure_examples": failure_examples(rows),
        "outcomes": {r["conv_id"]: [r["outcome"] + (":" + r["handoff_code"] if r["handoff_code"] else ""),
                                    int(r["correct"]), r["unsafe"]] for r in rows},
    }


def _group(rows, key):
    out: dict = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def failure_examples(rows: list[dict], per_category: int = 3) -> list[dict]:
    picked = []
    unsafe_first = sorted((r for r in rows if not r["correct"]), key=lambda r: (not r["unsafe"], r["conv_id"]))
    counts: Counter = Counter()
    for r in unsafe_first:
        if counts[r["category"]] >= per_category and not r["unsafe"]:
            continue
        counts[r["category"]] += 1
        picked.append({k: r[k] for k in ("conv_id", "category", "subcategory", "language", "gold_kind",
                                         "gold_reasons", "outcome", "handoff_code", "why_incorrect", "unsafe")}
                      | {"trace_ids": r["trace_ids"][-2:]})
    return picked[:60]


def compare(a: dict, b: dict) -> dict[str, Any]:
    ra, rb = a["rows"], b["rows"]
    return {
        "safe_automated_resolution": paired_bootstrap(ra, rb, lambda r: float(safe_automated(r)), lambda r: r["in_scope"]),
        "correct_outcome": paired_bootstrap(ra, rb, lambda r: float(r["correct"])),
        "unsafe": paired_bootstrap(ra, rb, lambda r: float(bool(r["unsafe"]))),
        "containment": paired_bootstrap(ra, rb, lambda r: float(r["outcome"] != "handoff")),
        "missed_transfers": paired_bootstrap(ra, rb, lambda r: float(r["outcome"] != "handoff"),
                                             lambda r: r["gold_kind"] == "handoff"),
    } | {f"{metric}_in_scope_pool_{b}": paired_bootstrap(ra, rb, fn, lambda r, b=b: r["in_scope"]
                                                          and pool_bucket_of(r["group"]) == b)
         for b in ("2-3", "4+")
         for metric, fn in (("safe_automated_resolution", lambda r: float(safe_automated(r))),
                            ("correct_outcome", lambda r: float(r["correct"])),
                            ("unsafe", lambda r: float(bool(r["unsafe"]))))}


def variance(runs: list[dict]) -> dict[str, Any]:
    """Repeated LLM runs on the variance subset: per-run metrics, spread, and how often the outcome repeats."""
    subset = [[r for r in run["rows"] if r["variance_subset"]] for run in runs]
    per_run = []
    for i, rows in enumerate(subset):
        s = summarize(rows, UNSAFE_TYPES, RUBRIC)
        per_run.append({"run": i + 1, "run_id": runs[i]["run_id"], "conversations": len(rows),
                        "correct_outcome": s["correct_outcome"], "safe_automated_resolution": s["safe_automated_resolution"],
                        "unsafe": s["unsafe"], "containment": s["containment"],
                        "cost_usd": cost(rows, s["safe_automated_resolution"]["k"])["llm_cost_usd_total"]})
    outcome = [{r["conv_id"]: (r["outcome"], r["handoff_code"]) for r in rows} for rows in subset]
    common = set.intersection(*(set(o) for o in outcome)) if outcome else set()
    same = sum(len({o[c] for o in outcome}) == 1 for c in common)

    def spread(key: str) -> dict[str, Any]:
        vals = [p[key]["rate"] for p in per_run if p[key]["rate"] is not None]
        mean = sum(vals) / len(vals) if vals else None
        sd = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else None
        return {"mean": _r4(mean), "sd": _r4(sd), "min": _r4(min(vals)) if vals else None,
                "max": _r4(max(vals)) if vals else None}
    return {"runs": per_run, "same_outcome_all_runs": {"k": same, "n": len(common)},
            "spread": {k: spread(k) for k in ("correct_outcome", "safe_automated_resolution", "unsafe", "containment")}}


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _r4(x):
    return None if x is None else round(x, 4)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--configs", default="rules,learned")
    ap.add_argument("--attentive", action="store_true", help="also run the attentive customer (deterministic configs)")
    ap.add_argument("--variance-runs", type=int, default=0, help="extra LLM runs on the variance subset")
    ap.add_argument("--workers", type=int, default=6, help="threads for the LLM configuration")
    ap.add_argument("--limit", type=int, default=0, help="debug: first N conversations only (not for results)")
    ap.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file; configs already in it are kept")
    ap.add_argument("--cap-usd", type=float, default=spend.CAP_USD, help="hard USD cap of the ledger for paid calls")
    ap.add_argument("--ledger", type=Path, default=SPEND_LEDGER, help="spend ledger the cap is counted on")
    args = ap.parse_args()
    manifest = check_frozen()
    suite = load_suite(SUITE_PATH)
    if args.limit:
        suite = suite[:args.limit]
    results = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() and not args.limit else {}
    results.update({"generated_by": "eval/run.py", "suite_sha256": manifest["suite_sha256"],
                    "warehouse_slice_content_hash": manifest["warehouse_slice_content_hash"],
                    "suite_conversations": len(suite)})
    results.setdefault("configs", {})
    ledger = spend.ledger(args.cap_usd, args.ledger)
    runs: dict[str, dict] = {}
    for name in [c for c in args.configs.split(",") if c]:
        workers = args.workers if ALL_CONFIGS[name]["llm"] else 1
        state = code_state()
        if ALL_CONFIGS[name]["llm"]:
            need = COST_PER_CONV_ESTIMATE * len(suite) * (1 + 0.25 * args.variance_runs)
            if spend.remaining(ledger) < need:
                raise SystemExit(f"estimated spend {need:.2f} USD exceeds the remaining cap "
                                 f"{spend.remaining(ledger):.2f} USD; nothing run")
        refused_before = ledger.status()["refused_calls"]
        run = run_config(name, suite, "compliant", workers, ledger if ALL_CONFIGS[name]["llm"] else None)
        runs[name] = run
        body = {"description": ALL_CONFIGS[name]["description"], "code": state, **aggregate(run)}
        if ALL_CONFIGS[name]["llm"]:
            body["budget_after_run"] = ledger.status()
            refused = ledger.status()["refused_calls"] - refused_before
            body["valid"] = refused == 0
            if refused:
                body["invalid_reason"] = f"{refused} calls refused by the USD cap: the run fell back to the parser"
            extra = [run]
            for k in range(args.variance_runs):
                sub = [s for s in suite if s["variance_subset"]]
                extra.append(run_config(name, sub, "compliant", workers, ledger, tag=f"-var{k + 2}"))
            if args.variance_runs:
                body["variance"] = variance(extra)
                body["budget_after_variance"] = ledger.status()
        if args.attentive and not ALL_CONFIGS[name]["llm"]:
            att = run_config(name, suite, "attentive", workers)
            body["attentive_customer"] = {"run_id": att["run_id"],
                                          "summary": summarize(att["rows"], UNSAFE_TYPES, RUBRIC),
                                          "by_category": aggregate(att)["by_category"]}
        results["configs"][name] = body
        print(json.dumps({name: {k: body["summary"][k] for k in ("correct_outcome", "safe_automated_resolution",
                                                                "unsafe", "containment")}}, indent=1))
    for name, body in results["configs"].items():
        if name not in runs and not args.limit:
            path = RUNS_DIR / body["run_id"] / "rows.jsonl"
            if path.exists():
                runs[name] = {"rows": [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]}
    for a, b in (("rules", "learned"), ("learned", "llm"), ("rules", "llm"), ("label_rule", "learned"),
                 ("rules", "label_rule")):
        if a in runs and b in runs:
            results.setdefault("comparisons", {})[f"{b}_minus_{a}"] = compare(runs[a], runs[b])
    if any(ALL_CONFIGS[n]["llm"] for n in runs if n in ALL_CONFIGS) or "spend" not in results:
        results["spend"] = ledger.status() | {"cap_usd": args.cap_usd, "ledger": _rel(args.ledger)}
    if not args.limit:
        args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                newline="\n")


if __name__ == "__main__":
    main()
