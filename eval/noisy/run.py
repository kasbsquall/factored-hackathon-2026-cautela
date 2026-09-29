"""Run the noisy-customer slice through the whole agent for each configuration: eval/noisy/<half>/results.json.

    uv run python -m eval.noisy.run --configs rules,learned       # dev half, no network, no cost
    uv run python -m eval.noisy.run --configs llm                 # gpt-6-luna on its own ledger, USD 1.00 cap
    uv run python -m eval.noisy.run --half sealed --configs rules # after the code freeze only, once per config

Same configurations, harness, judge and metrics as eval/run.py (rules, learned, llm), same compliant customer. Before
anything runs the suite, gold, policy version and warehouse slice are checked against the half's manifest, and the
gold file against the gold the suite carries. The sealed half also needs the generator source to be the frozen one
(sha256 in its manifest) and refuses a configuration that already has results there.

Reported per configuration, overall, by noise family and by language, each with its denominator:
  correct                    the judge's correct outcome (eval/judge.py)
  safe automated resolution  eval/metrics.py's definition; every conversation here is in scope
  safe handoff               gold is a resolution, the conversation handed off, nothing was written and nothing
                             unsafe happened: a missed automation, not a harm (reported apart from correct and
                             unsafe; its denominator is the conversations whose gold is resolved)
  unsafe                     any unsafe event of the judge (wrong-charge write first among them)
  other incorrect            the rest: a wrong reason code, a missing review case, a conversation left pending
Failures list every incorrect conversation with its trace ids, the noise field and form, and whether the customer
was asked for details (the only way a restatement is used). Full transcripts go to data/eval/runs/ (git-ignored).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb

from eval import budget as spend
from eval.judge import RUBRIC, UNSAFE_TYPES, load_suite
from eval.metrics import count_rate, cost, latency, paired_bootstrap, safe_automated, summarize
from eval.noisy.build import FAMILIES, check_split, generator_sha256, gold_rows, serialize
from eval.oracle import rules
from eval.paths import DATA_DIR, NOISY_DEV_DIR, NOISY_SEALED_DIR, RUNS_DIR
from eval.run import CONFIGS, COST_PER_CONV_ESTIMATE, _rel, code_state, run_config
from eval.slice import content_hash

HALVES = {"dev": NOISY_DEV_DIR, "sealed": NOISY_SEALED_DIR}
LEDGER = DATA_DIR / "llm_spend_noisy.json"
CAP_USD = 1.00


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_frozen(half_dir: Path) -> tuple[dict, list[dict], Path]:
    manifest_path = half_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} not found: build the half with `python -m eval.noisy.build` first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sealed = manifest["role"] == "sealed"
    slice_path, _ = check_split(manifest["split"], allow_sealed=sealed)
    suite_path, gold_path = half_dir / "suite.jsonl", half_dir / "gold.jsonl"
    if not suite_path.exists():
        raise SystemExit(f"{suite_path} not found: run `uv run python -m eval.noisy.build --split "
                         f"{manifest['split']} --verify`")
    if _sha(suite_path) != manifest["suite_sha256"] or _sha(gold_path) != manifest["gold_sha256"]:
        raise SystemExit("suite.jsonl or gold.jsonl does not match the sha256 frozen in the manifest")
    suite = load_suite(suite_path)
    if serialize(gold_rows(suite)).encode() != gold_path.read_bytes().replace(b"\r\n", b"\n"):
        raise SystemExit("gold.jsonl disagrees with the gold carried by suite.jsonl")
    if rules()["version"] != manifest["policy_version"]:
        raise SystemExit(f"rules.yaml is version {rules()['version']}, the gold was set under "
                         f"{manifest['policy_version']}; nothing run")
    if sealed and generator_sha256()["combined"] != manifest["generator_sha256"]["combined"]:
        raise SystemExit("the generator source differs from the frozen one recorded in the sealed manifest")
    with duckdb.connect(str(slice_path), read_only=True) as con:
        if content_hash(con) != manifest["warehouse_slice_content_hash"]:
            raise SystemExit(f"{slice_path} is not the warehouse slice the half was built on")
    return manifest, suite, slice_path


# ---- per-conversation rows ------------------------------------------------------------------------------------
def details_asked(run_id: str, suite: list[dict]) -> dict[str, bool]:
    """Whether the customer's restatement was sent (it is sent only when the service asks for details)."""
    said = {c["conv_id"]: c["customer"]["restatement"] for c in suite}
    out = {}
    path = RUNS_DIR / run_id / "transcripts.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            t = json.loads(line)
            out[t["conv_id"]] = any(c["kind"] == "turn" and c["answer"] == said[t["conv_id"]] for c in t["calls"])
    return out


def safe_handoff(r: dict) -> bool:
    return (r["gold_kind"] == "resolved" and r["outcome"] == "handoff" and not r["unsafe"]
            and r["wrote_case_on"] is None)


def other_incorrect(r: dict) -> bool:
    return not r["correct"] and not r["unsafe"] and not safe_handoff(r)


def noisy_rows(run: dict, suite: list[dict]) -> list[dict]:
    by_id = {c["conv_id"]: c for c in suite}
    asked = details_asked(run["run_id"], suite)
    rows = []
    for r in run["rows"]:
        noise = by_id[r["conv_id"]]["noise"]
        # field, form and where only: the stated values themselves are organizer-derived and stay in the suite
        altered = [{k: a[k] for k in ("field", "where", "form", "mode", "added") if k in a} for a in noise["altered"]]
        rows.append(r | {"noise_family": noise["family"], "altered": altered,
                         "restatement_used": asked.get(r["conv_id"], False)})
    return rows


# ---- aggregates -----------------------------------------------------------------------------------------------
def outcome_split(rows: list[dict]) -> dict[str, Any]:
    return {"conversations": len(rows),
            "correct": count_rate(rows, lambda r: r["correct"]),
            "safe_automated_resolution": count_rate(rows, safe_automated),
            "safe_handoff": count_rate(rows, safe_handoff, lambda r: r["gold_kind"] == "resolved"),
            "unsafe": count_rate(rows, lambda r: bool(r["unsafe"])),
            "other_incorrect": count_rate(rows, other_incorrect),
            "wrong_charge_write": count_rate(rows, lambda r: "wrong_charge_write" in r["unsafe"]),
            "gold_resolved": sum(r["gold_kind"] == "resolved" for r in rows),
            "restatement_used": count_rate(rows, lambda r: r["restatement_used"]),
            "outcomes": dict(Counter(r["outcome"] + (":" + r["handoff_code"] if r["handoff_code"] else "")
                                     for r in rows).most_common())}


def failures(rows: list[dict]) -> list[dict]:
    keys = ("conv_id", "noise_family", "language", "gold_kind", "gold_reasons", "outcome", "handoff_code",
            "why_incorrect", "unsafe", "clarify_rounds", "restatement_used", "altered", "trace_ids")
    bad = sorted((r for r in rows if not r["correct"]), key=lambda r: (not r["unsafe"], r["noise_family"], r["conv_id"]))
    return [{k: r[k] for k in keys} | {"safe_handoff": safe_handoff(r)} for r in bad]


def aggregate(run: dict, rows: list[dict]) -> dict[str, Any]:
    summary = summarize(rows, UNSAFE_TYPES, RUBRIC)

    def by(key) -> dict[str, Any]:
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        return {g: outcome_split(rs) for g, rs in sorted(groups.items())}
    return {"run_id": run["run_id"], "wall_s": run["wall_s"], "workers": run["workers"],
            "disposition_model": run["disposition_model"], "disposition_source": run["disposition_source"],
            "noisy_summary": outcome_split(rows), "by_family": by(lambda r: r["noise_family"]),
            "by_language": by(lambda r: r["language"]),
            "by_family_language": by(lambda r: f"{r['noise_family']}/{r['language']}"),
            "summary": summary, "latency": latency(rows),
            "cost": cost(rows, summary["safe_automated_resolution"]["k"]),
            "harness_errors": sum(bool(r["harness_error"]) for r in rows),
            "llm_fallbacks": {"llm_failed_calls": sum(r["llm_failed"] for r in rows),
                              "understand_sources": dict(sum((Counter(r["understand_sources"]) for r in rows),
                                                             Counter()))},
            "failures": failures(rows),
            "outcomes": {r["conv_id"]: [r["outcome"] + (":" + r["handoff_code"] if r["handoff_code"] else ""),
                                        int(r["correct"]), r["unsafe"]] for r in rows}}


def compare(a: list[dict], b: list[dict]) -> dict[str, Any]:
    """b minus a on the same conversations, overall and per family, paired bootstrap over source groups."""
    metrics = {"correct": lambda r: float(r["correct"]), "safe_automated_resolution": lambda r: float(safe_automated(r)),
               "safe_handoff": lambda r: float(safe_handoff(r)), "unsafe": lambda r: float(bool(r["unsafe"]))}
    out = {m: paired_bootstrap(a, b, fn) for m, fn in metrics.items()}
    out["by_family"] = {f: {m: paired_bootstrap(a, b, fn, lambda r, f=f: r["noise_family"] == f)
                            for m, fn in metrics.items()} for f in FAMILIES}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--half", choices=sorted(HALVES), default="dev")
    ap.add_argument("--configs", default="rules,learned")
    ap.add_argument("--workers", type=int, default=6, help="threads for the LLM configuration")
    ap.add_argument("--cap-usd", type=float, default=CAP_USD, help="hard USD cap of the ledger for paid calls")
    ap.add_argument("--ledger", type=Path, default=LEDGER, help="spend ledger the cap is counted on")
    args = ap.parse_args()
    half_dir = HALVES[args.half]
    manifest, suite, slice_path = check_frozen(half_dir)
    out = half_dir / "results.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    names = [c for c in args.configs.split(",") if c]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configurations {unknown}: one of {sorted(CONFIGS)}")
    if manifest["role"] == "sealed" and (ran := [n for n in names if n in results.get("configs", {})]):
        raise SystemExit(f"the sealed half already ran {ran}; it runs once per configuration")
    results.update({"generated_by": "eval/noisy/run.py", "half": args.half, "split": manifest["split"],
                    "suite_sha256": manifest["suite_sha256"], "gold_sha256": manifest["gold_sha256"],
                    "generator_sha256": manifest["generator_sha256"]["combined"],
                    "warehouse_slice_content_hash": manifest["warehouse_slice_content_hash"],
                    "suite_conversations": len(suite)})
    results.setdefault("configs", {})
    ledger = spend.ledger(args.cap_usd, args.ledger)
    rows_by: dict[str, list[dict]] = {}
    for name in names:
        paid = CONFIGS[name]["llm"]
        if paid and spend.remaining(ledger) < COST_PER_CONV_ESTIMATE * len(suite):
            raise SystemExit(f"estimated spend exceeds the remaining cap {spend.remaining(ledger):.2f} USD; nothing run")
        state = code_state(output=out)
        refused_before = ledger.status()["refused_calls"]
        run = run_config(name, suite, "compliant", args.workers if paid else 1, ledger if paid else None,
                         tag=f"-noisy-{args.half}", slice_path=slice_path)
        rows_by[name] = noisy_rows(run, suite)
        body = {"description": CONFIGS[name]["description"], "code": state, **aggregate(run, rows_by[name])}
        if paid:
            refused = ledger.status()["refused_calls"] - refused_before
            body["budget_after_run"] = ledger.status()
            body["valid"] = refused == 0
            if refused:
                body["invalid_reason"] = f"{refused} calls refused by the USD cap: the run fell back to the parser"
        results["configs"][name] = body
        print(json.dumps({name: {k: body["noisy_summary"][k] for k in
                                 ("correct", "safe_automated_resolution", "safe_handoff", "unsafe")}}, indent=1))
    for name, body in results["configs"].items():
        if name not in rows_by:
            path = RUNS_DIR / body["run_id"] / "rows.jsonl"
            if path.exists():
                run = {"run_id": body["run_id"], "rows": [json.loads(x) for x in
                                                          path.read_text(encoding="utf-8").splitlines() if x]}
                rows_by[name] = noisy_rows(run, suite)
    for a, b in (("rules", "learned"), ("learned", "llm"), ("rules", "llm")):
        if a in rows_by and b in rows_by:
            results.setdefault("comparisons", {})[f"{b}_minus_{a}"] = compare(rows_by[a], rows_by[b])
    if any(CONFIGS[n]["llm"] for n in names) or "spend" not in results:
        results["spend"] = ledger.status() | {"cap_usd": args.cap_usd, "ledger": _rel(args.ledger)}
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
