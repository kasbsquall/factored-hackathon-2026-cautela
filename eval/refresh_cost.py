"""Recompute the cost block and the safe-resolution ceiling of eval/results.json from the saved per-conversation rows.

    uv run python -m eval.refresh_cost

The first cost block divided the LLM spend of a run by every conversation and called it "per attempted
conversation". eval/metrics.py now divides by the in-scope conversations where automation was attempted, and keeps
the old quantity as usd_per_conversation. This rewrites the cost block and adds the safe-resolution ceiling to the
summary and to the by_* breakdowns, from data/eval/runs/<run id>/rows.jsonl (git-ignored). No agent, judge or LLM
call is made. The script stops if the rows do not reproduce the recorded LLM spend or any recorded breakdown field,
so every other number in the file stays as recorded.
"""

from __future__ import annotations

import json

from eval.metrics import breakdown, count_rate, cost, eligible, in_scope
from eval.paths import RESULTS_PATH, RUNS_DIR
from eval.pools import pool_bucket_of


def breakdowns(rows: list[dict]) -> dict:
    in_scope_rows = [r for r in rows if in_scope(r)]
    return {"by_language": breakdown(rows, "language"), "by_country": breakdown(rows, "country"),
            "by_segment": breakdown(rows, "segment"), "by_language_in_scope": breakdown(in_scope_rows, "language"),
            "by_pool_bucket_in_scope": breakdown([r | {"pool_bucket": pool_bucket_of(r["group"])} for r in in_scope_rows
                                                  if pool_bucket_of(r["group"])], "pool_bucket")}


def _check_unchanged(name: str, key: str, old: dict, new: dict) -> None:
    """Every recorded field must come out the same; only the ceiling is added."""
    def strip(d: dict) -> dict:
        return {g: {k: v for k, v in b.items() if k != "safe_automated_resolution_ceiling"} for g, b in d.items()}
    if strip(new) != strip(old):
        raise SystemExit(f"{name}.{key}: the saved rows do not reproduce the recorded breakdown")


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    for name, body in results["configs"].items():
        path = RUNS_DIR / body["run_id"] / "rows.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
        if len(rows) != body["summary"]["conversations"]:
            raise SystemExit(f"{path} has {len(rows)} rows, results.json says {body['summary']['conversations']}")
        new = cost(rows, body["summary"]["safe_automated_resolution"]["k"])
        if new["llm_cost_usd_total"] != body["cost"]["llm_cost_usd_total"]:
            raise SystemExit(f"{name}: the saved rows do not reproduce the recorded LLM spend")
        body["cost"] = new
        body["summary"]["safe_automated_resolution_ceiling"] = count_rate(rows, eligible, in_scope)
        for key, new_breakdown in breakdowns(rows).items():
            _check_unchanged(name, key, body[key], new_breakdown)
            body[key] = new_breakdown
        print(name, {k: new[k] for k in ("attempted_conversations", "usd_per_conversation",
                                         "usd_per_attempted_conversation")})
    results["cost_refreshed_by"] = "eval/refresh_cost.py"
    RESULTS_PATH.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
