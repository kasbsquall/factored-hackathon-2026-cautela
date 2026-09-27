"""Aggregates of the intermediate fix-round runs, from their saved rows, for the history in eval/report.md.

    uv run python -m eval.fix_rounds     # -> eval/results_fix_rounds.json

The runs between the original evaluation (eval/results.json) and the final after-fix rerun
(eval/results_after_fix.json) were kept under data/eval/runs/ (git-ignored). This recomputes their aggregates with
the current eval/metrics.py from rows.jsonl, without calling the agent, the judge or the LLM, so the fix-round
tables of the report come from a committed file. Runs whose rows were not kept (the first after-fix run of every
configuration, including the only after-fix LLM run before the final one) cannot be listed.
"""

from __future__ import annotations

import json

from eval.paths import EVAL_DIR, RUNS_DIR
from eval.run import aggregate, compare

OUT = EVAL_DIR / "results_fix_rounds.json"
ROUNDS = {  # label -> run id per configuration
    "first_round_rerun": {"rules": "rules-compliant-20260926T225250Z", "learned": "learned-compliant-20260926T225524Z"},
    "second_round": {"rules": "rules-compliant-20260927T000907Z", "learned": "learned-compliant-20260927T001117Z"},
}
NOTES = {
    "first_round_rerun": "rerun of the first fix round at commit 55222bc (fixes 785029b, be506c4, 80c21fd, 773a62b)",
    "second_round": "bounded clarification (672c33d) and options before abstaining (4f630cc), LLM off",
}


def rows_of(run_id: str) -> list[dict]:
    path = RUNS_DIR / run_id / "rows.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def main() -> None:
    out: dict = {"generated_by": "eval/fix_rounds.py", "source": "data/eval/runs/<run id>/rows.jsonl (git-ignored)",
                 "rounds": {}}
    loaded: dict = {}
    for label, runs in ROUNDS.items():
        out["rounds"][label] = {"note": NOTES[label], "configs": {}}
        for cfg, run_id in runs.items():
            run = {"run_id": run_id, "customer": "compliant", "wall_s": None, "workers": 1, "rows": rows_of(run_id)}
            loaded[(label, cfg)] = run
            body = aggregate(run)
            body.pop("outcomes")
            body.pop("failure_examples")
            out["rounds"][label]["configs"][cfg] = body
    out["second_round_minus_first_round_rerun"] = {
        cfg: compare(loaded[("first_round_rerun", cfg)], loaded[("second_round", cfg)]) for cfg in ("rules", "learned")}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
