"""Re-score saved runs with the current judge, without calling the agent or the LLM again.

    uv run python -m eval.rejudge

Every run listed in eval/results.json (main, attentive and variance runs) keeps its full transcripts under
data/eval/runs/<run id>/transcripts.jsonl. This rebuilds each Transcript, judges it against the frozen suite,
rewrites rows.jsonl in suite order and recomputes the aggregates, variance and paired comparisons. Run metadata
(run ids, wall time, budget status, validity) is kept as recorded. Used when a judge fix lands after a paid run;
the report says which fixes were applied this way.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from eval.harness import Call, Transcript
from eval.judge import RUBRIC, UNSAFE_TYPES, judge, load_suite, owners
from eval.metrics import summarize
from eval.paths import RESULTS_PATH, RUNS_DIR, SUITE_PATH
from eval.repeat import security_repeats
from eval.run import aggregate, check_frozen, compare, row_of, variance

CALL_FIELDS = {f.name for f in fields(Call)}
TRANSCRIPT_FIELDS = {f.name for f in fields(Transcript)}


def transcript_of(raw: dict[str, Any]) -> Transcript:
    body = {k: v for k, v in raw.items() if k in TRANSCRIPT_FIELDS}
    body["calls"] = [Call(**{k: v for k, v in c.items() if k in CALL_FIELDS}) for c in raw.get("calls", [])]
    return Transcript(**body)


def rejudge_run(run_id: str, specs: dict[str, dict], owner: dict[str, str]) -> list[dict]:
    out_dir = RUNS_DIR / run_id
    raws = [json.loads(x) for x in (out_dir / "transcripts.jsonl").read_text(encoding="utf-8").splitlines() if x]
    by_id = {r["conv_id"]: transcript_of(r) for r in raws}
    order = [cid for cid in specs if cid in by_id]
    rows = [row_of(specs[cid], by_id[cid], judge(specs[cid], by_id[cid], owner)) for cid in order]
    (out_dir / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8", newline="\n")
    return rows


def main() -> None:
    check_frozen()
    specs = {s["conv_id"]: s for s in load_suite(SUITE_PATH)}
    owner = owners()
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    runs: dict[str, dict] = {}
    for name, body in results["configs"].items():
        meta = {k: body[k] for k in ("run_id", "customer", "wall_s", "workers")}
        run = meta | {"rows": rejudge_run(body["run_id"], specs, owner)}
        runs[name] = run
        keep = {k: v for k, v in body.items() if k in ("description", "budget_after_run", "valid", "invalid_reason",
                                                       "budget_after_variance")}
        new = {"description": keep.pop("description"), **aggregate(run), **keep}
        if "attentive_customer" in body:
            att_id = body["attentive_customer"]["run_id"]
            att = {"run_id": att_id, "customer": "attentive", "wall_s": None, "workers": 1,
                   "rows": rejudge_run(att_id, specs, owner)}
            new["attentive_customer"] = {"run_id": att_id, "summary": summarize(att["rows"], UNSAFE_TYPES, RUBRIC),
                                         "by_category": aggregate(att)["by_category"]}
        if "variance" in body:
            extra = [run] + [{"run_id": r["run_id"], "rows": rejudge_run(r["run_id"], specs, owner)}
                             for r in body["variance"]["runs"][1:]]
            new["variance"] = variance(extra)
        if "security_repeats" in body:
            old = body["security_repeats"]
            extra = [run] + [{"run_id": r["run_id"], "rows": rejudge_run(r["run_id"], specs, owner)}
                             for r in old["runs"][1:]]
            new["security_repeats"] = security_repeats(extra) | {k: old[k] for k in ("refused_calls", "valid")}
        results["configs"][name] = new
        print(name, {k: new["summary"][k]["k"] for k in ("correct_outcome", "safe_automated_resolution", "unsafe",
                                                         "handoff_fully_valid")})
    results["comparisons"] = {}
    for a, b in (("rules", "learned"), ("learned", "llm"), ("rules", "llm")):
        if a in runs and b in runs:
            results["comparisons"][f"{b}_minus_{a}"] = compare(runs[a], runs[b])
    results["rejudged_by"] = "eval/rejudge.py"
    RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
