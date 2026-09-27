"""What the LLM extraction adds over the deterministic parser, measured on a saved run of the llm configuration.

    uv run python -m eval.llm_extraction      # runs named in eval/results_after_fix.json -> eval/llm_extraction.json

For every customer message the model read (audit step `orchestrator.understand` with outcome `llm`), the parser is
run again offline on the same message and service date, and the two readings are compared: which amounts, dates
and merchants only the model supplied, and where the two disagree on the intent. The merged reading keeps the
parser's amount, currency and date whenever the parser read one (`agent/orchestrator/intent.merge`), so the model can
add a value but never replace one. On the first message of a dispute conversation the added values are checked
against the hints the description was written from (eval/cases/disputes/test.jsonl, the spent test split). The
end-to-end difference comes from the paired comparison `llm_minus_learned` of the same results file.

Reads git-ignored files only (data/eval/runs/<run id>/, the suite and the test cases) and writes counts and suite
conversation ids, no message text. No agent or model call is made.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from typing import Any

from agent.orchestrator import intent as nlu
from eval.judge import load_suite
from eval.paths import DISPUTES_DIR, EVAL_DIR, RUNS_DIR, SUITE_PATH

AFTER_FIX = EVAL_DIR / "results_after_fix.json"
OUT = EVAL_DIR / "llm_extraction.json"
FIRST_TURN_CATEGORIES = ("dispute", "multilingual")
EXAMPLES = 8


def _jsonl(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def messages(run_id: str) -> dict[str, dict[str, Any]]:
    """trace id -> the customer message of that turn, its conversation, turn index and options shown before it."""
    out = {}
    for t in _jsonl(RUNS_DIR / run_id / "transcripts.jsonl"):
        prev_options, turn = 0, 0
        for call in t["calls"]:
            if call["kind"] == "turn":
                out[call["trace_id"]] = {"conv_id": t["conv_id"], "text": call["answer"], "turn": turn,
                                         "n_options": prev_options}
                turn += 1
            prev_options = len(call.get("options") or [])
    return out


def model_readings(run_id: str) -> list[dict[str, Any]]:
    """The merged reading of every message the model read, with the service date it was read on."""
    out = []
    for rec in _jsonl(RUNS_DIR / run_id / "audit.jsonl"):
        if rec["step"] == "orchestrator.understand" and rec["outcome"] == "llm":
            out.append({"trace_id": rec["trace_id"], "today": date.fromisoformat(rec["ts"][:10]),
                        **rec["masked_args"]})
    return out


def _amount_ok(value: float | None, hint: dict | None) -> bool | None:
    if value is None or hint is None:
        return None
    return abs(value / hint["claimed"] - 1) < 0.01


def _date_ok(value: str | None, hint: dict | None) -> bool | None:
    if value is None or hint is None:
        return None
    return hint["lo"] <= value <= hint["hi"]


def compare(run_id: str, suite: dict[str, dict], cases: dict[str, dict]) -> dict[str, Any]:
    msgs = messages(run_id)
    added: Counter = Counter()
    intents: Counter = Counter()
    first: Counter = Counter()
    examples: dict[str, list[str]] = {}

    def note(key: str, conv_id: str) -> None:
        first[key] += 1
        if len(examples.setdefault(key, [])) < EXAMPLES:
            examples[key].append(conv_id)

    readings = model_readings(run_id)
    for r in readings:
        m = msgs.get(r["trace_id"])
        if m is None:
            continue
        parser = nlu.parse_intent(m["text"], r["today"], m["n_options"])
        p_date = parser.date.isoformat() if parser.date else None
        for field, p_val in (("amount", parser.amount), ("date", p_date), ("merchant", parser.merchant)):
            if p_val is None and r.get(field) is not None:
                added[f"model_only_{field}"] += 1
            elif p_val is not None:
                added[f"parser_{field}"] += 1
        if parser.intent != r.get("intent"):
            intents[f"{parser.intent} -> {r.get('intent')}"] += 1
        spec = suite.get(m["conv_id"], {})
        case = cases.get(spec.get("source_case_id"))
        if m["turn"] != 0 or spec.get("category") not in FIRST_TURN_CATEGORIES or case is None:
            continue
        hints = case["hints"]
        first["first_dispute_messages"] += 1
        for field, p_val, check in (("amount", parser.amount, _amount_ok), ("date", p_date, _date_ok)):
            hint = hints.get(field)
            if hint is not None:
                first[f"{field}_in_hints"] += 1
            if p_val is not None:
                ok = check(p_val, hint)
                note(f"parser_{field}_{'matches_hint' if ok else 'wrong_or_not_in_hints'}", m["conv_id"])
            elif r.get(field) is not None:
                ok = check(r[field], hint)
                note(f"model_added_{field}_{'matches_hint' if ok else 'wrong_or_not_in_hints'}", m["conv_id"])
            elif hint is not None:
                note(f"{field}_in_hints_read_by_neither", m["conv_id"])
    return {"model_readings": len(readings), "values": dict(sorted(added.items())),
            "intent_changes": dict(intents.most_common()), "first_dispute_message": dict(sorted(first.items())),
            "examples": {k: v for k, v in sorted(examples.items())}}


def main() -> None:
    res = json.loads(AFTER_FIX.read_text(encoding="utf-8"))
    llm, learned = res["configs"]["llm"], res["configs"]["learned"]
    suite = {s["conv_id"]: s for s in load_suite(SUITE_PATH)}
    cases = {c["case_id"]: c for c in _jsonl(DISPUTES_DIR / "test.jsonl")}
    out = {"generated_by": "eval/llm_extraction.py", "source": "eval/results_after_fix.json",
           "llm_run": llm["run_id"], "llm_code": llm.get("code"), "learned_run": learned["run_id"],
           "extraction": compare(llm["run_id"], suite, cases),
           "end_to_end_llm_minus_learned": res.get("comparisons", {}).get("llm_minus_learned"),
           "correct_by_category": {cat: {"learned": learned["by_category"][cat]["correct"],
                                         "llm": llm["by_category"][cat]["correct"],
                                         "conversations": llm["by_category"][cat]["conversations"]}
                                   for cat in llm["by_category"]},
           "cost": llm["cost"], "latency": {"llm": llm["latency"], "learned": learned["latency"]}}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out["extraction"], indent=1))


if __name__ == "__main__":
    main()
