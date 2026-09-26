"""Render ml/reports/results.md from results.json, fitted.json and the case manifest.

    uv run python -m ml.report

No number is typed by hand: every figure below is read from those JSON files.
"""

from __future__ import annotations

import json

from ml import report_changes
from ml.data import CASES_DIR, REPORTS_DIR

SYSTEM_NOTES = {
    "rules_fixed": "rules ranker + the fixed clarify rule from agent/tools/ranking.py (top < 0.60 or margin < 0.15)",
    "rules_tuned": "rules ranker + calibrated confidence (train) + thresholds tuned on val (baseline)",
    "learned_ranker_calibrated": "learned ranker + calibrated confidence + val thresholds (ablation)",
    "learned_ranker_disposition": "learned ranker + learned disposition model + val thresholds (proposed)",
}


def _v(entry: dict, pct: bool = True) -> str:
    if entry is None or entry.get("value") is None:
        return "n/a"
    f = (lambda x: f"{100 * x:.1f}%") if pct else (lambda x: f"{x:.3f}")
    ci = entry.get("ci95")
    return f"{f(entry['value'])} [{f(ci[0])}, {f(ci[1])}]" if ci else f(entry["value"])


def _frac(d: dict) -> str:
    return f"{d['count']} / {d['denominator']}"


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def main_table(res: dict) -> str:
    rows = ["| system | top-1 (match) | MRR | top-3, pools >= 4 | ECE | correct decisions | unsafe | safe automated "
            "resolution | automation attempted | containment |", "|" + "---|" * 10]
    for name, body in res["systems"].items():
        s = body["summary"]
        rows.append(f"| `{name}` | {_v(s['top1_accuracy'])} | {_v(s['mrr'], False)} | {_v(s['top3_recall_pool_ge4'])} "
                    f"| {_v(s['ece'], False)} | {_v(s['correct_decision_rate'])} | {_frac(s['unsafe'])} "
                    f"({_v(s['unsafe_rate'])}) | {_v(s['safe_automated_resolution_rate'])} "
                    f"| {_pct(s['automation_attempted_share'])} | {_pct(s['containment_rate'])} |")
    return "\n".join(rows)


def label_table(res: dict) -> str:
    rows = ["| system | label | n | act | clarify | abstain | correct | unsafe |", "|" + "---|" * 8]
    for name, body in res["systems"].items():
        for lab, d in body["summary"]["per_label"].items():
            dec, out = d["decisions"], d["outcomes"]
            rows.append(f"| `{name}` | {lab} | {d['n']} | {dec.get('act', 0)} | {dec.get('clarify', 0)} | "
                        f"{dec.get('abstain', 0)} | {out.get('correct', 0)} | {out.get('unsafe', 0)} |")
    return "\n".join(rows)


def escalation_table(res: dict) -> str:
    rows = ["| system | missed transfers (no_match not handed off) | unnecessary transfers (match or ambiguous "
            "handed off) | recall-error matches acted on correctly |", "|---|---|---|---|"]
    for name, body in res["systems"].items():
        s = body["summary"]
        rec = s["recall_error_match"]
        rows.append(f"| `{name}` | {_frac(s['missed_transfers'])} | {_frac(s['unnecessary_transfers'])} "
                    f"| {rec['acted_correctly']} / {rec['n']} |")
    return "\n".join(rows)


def diff_table(res: dict) -> str:
    rows = ["| comparison | metric | difference | 95% CI | cases |", "|---|---|---|---|---|"]
    for comp, diffs in res["paired_differences"].items():
        for metric, d in diffs.items():
            rows.append(f"| {comp} | {metric} | {100 * d['diff']:+.1f} pts | [{100 * d['ci95'][0]:+.1f}, "
                        f"{100 * d['ci95'][1]:+.1f}] | {d['n_cases']} |")
    return "\n".join(rows)


def breakdown_table(res: dict, key: str, systems: tuple[str, ...]) -> str:
    rows = [f"| {key} | n | " + " | ".join(f"`{s}` correct / unsafe / top-1" for s in systems) + " |",
            "|---|---|" + "---|" * len(systems)]
    groups = res["systems"][systems[0]]["breakdowns"][key]
    for g in groups:
        cells = []
        for s in systems:
            b = res["systems"][s]["breakdowns"][key][g]
            cells.append(f"{_pct(b['correct_decision_rate'])} / {b['unsafe']['count']} / {_pct(b['top1_accuracy'])}")
        rows.append(f"| {g} | {groups[g]['n']} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def curve_table(res: dict, name: str) -> str:
    rows = ["| confidence threshold | coverage (share acted) | selective accuracy | acted |", "|---|---|---|---|"]
    for p in res["systems"][name]["selective_curve"]:
        rows.append(f"| {p['threshold']:.3f} | {_pct(p['coverage'])} | {_pct(p['selective_accuracy'])} | {p['n_acted']} |")
    return "\n".join(rows)


def parse_table(res: dict) -> str:
    rows = ["| language / family | amount read | date read |", "|---|---|---|"]
    for key, d in res["parser_cue_recovery"].items():
        rows.append(f"| {key} | {d['amount_read']} / {d['amount_given']} | {d['date_read']} / {d['date_given']} |")
    return "\n".join(rows)


def examples_block(res: dict, name: str, limit: int = 6) -> str:
    out = []
    ex = res["examples"][name]
    for kind in ("unsafe", "wrong_top1_on_match"):
        for e in ex[kind][:limit]:
            top = "; ".join(f"{t['amount']} {t['currency']} {t['transaction_type']} {t['merchant_name'] or '-'} "
                            f"{str(t['transaction_date'])[:10]} (score {t['score']}{', target' if t['is_target'] else ''})"
                            for t in e["top3"])
            out.append(f"* `{e['case_id']}` [{kind}, label {e['label']}, {e['family']}, decision {e['decision']}, "
                       f"confidence {e['confidence']:.3f}] \"{e['description']}\"  \n  top 3: {top}")
    return "\n".join(out)


def _floor_note(res: dict) -> str:
    abl = res.get("act_floor_ablation", {})
    binding = [n for n, d in abl.items() if d["val_t_act"] < d["act_floor"]]
    note = ("The floor binds for: " + ", ".join(f"`{n}`" for n in binding) + "." if binding else
            "In this run every val-tuned act threshold is already above the floor, so the floor does not bind "
            "and costs nothing on test; it guards against a refit that lands on a lower threshold.")
    prev = report_changes.previous_thresholds_below(min((d["act_floor"] for d in abl.values()), default=0.0))
    if prev:
        note += (" In the previous run the val search chose " + ", ".join(f"t_act = {t} for `{n}`" for n, t in prev)
                 + ", below the floor; the floor would have bound there.")
    return note


def render() -> str:
    res = json.loads((REPORTS_DIR / "results.json").read_text(encoding="utf-8"))
    fit = json.loads((REPORTS_DIR / "fitted.json").read_text(encoding="utf-8"))
    man = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))
    proposed = res["proposed_system"]
    s = res["systems"][proposed]["summary"]
    llm = res["llm"]
    llm_line = (f"**LLM ranker: not run** ({llm['reason']}). The code path exists (`ml/rankers/llm.py`, prompt "
                "`rank_v1`, default model `claude-sonnet-5`); with a key, `ml.evaluate` runs it on a stratified "
                "test subset three times and reports variance and cost." if llm["status"] != "run"
                else f"LLM ranker run: model `{llm['model']}`, prompt `{llm['prompt_version']}`, see results.json.")
    parts = [
        "# Results: which charge is the customer disputing?\n",
        "Offline evaluation on the held-out test split. Generated by `ml/report.py` from "
        "`ml/reports/results.json`, `ml/reports/fitted.json` and `eval/cases/disputes/manifest.json`; "
        f"data version `{res['data_version']}`. These are offline measurements on generated scenarios over "
        "organizer transactions, not production results.\n",
        "## Workload\n",
        f"* Test cases: {s['n_cases']} ({s['n_groups']} Spanish sources, the rest are their team-generated "
        f"Portuguese renderings). Labels: {s['n_by_label']}.",
        f"* Splits: train {man['counts']['train']}, val {man['counts']['val']}, test {man['counts']['test']} cases; "
        "customers disjoint across splits; report dates disjoint in time; families F5 and F6 only in test.",
        "* Label quality: valid by construction from explicit hints and tolerances (see `ml/DATASHEET.md`); "
        "not human-annotated; a system can partly learn the generator, which the held-out families measure.",
        f"* Ranker model: `{fit['selected_ranker_model']}` (val MRR and log loss: see fitted.json). Disposition "
        f"model: `{res['systems'][proposed]['decider_params']['model']}`. Thresholds fitted on val with an unsafe "
        f"rate cap of {fit['max_unsafe_rate']:.0%}.",
        f"* {llm_line}",
        "* Confidence intervals: 95% percentile bootstrap, 1,000 resamples of Spanish source groups (a case "
        "and its Portuguese twin move together).\n",
        "## Systems\n",
        "\n".join(f"* `{k}`: {v}" for k, v in SYSTEM_NOTES.items()) + "\n",
        "## Main results (test)\n",
        "Top-1, MRR and top-3 are measured on `match` cases. Correct decision: act on the target for `match`, "
        "clarify with the target listed for `ambiguous`, abstain for `no_match`. Unsafe: acting on a charge when "
        "the case was ambiguous, had no match, or acting on the wrong one. Safe automated resolution: correct act "
        "without a clarifying turn, over all test cases. Containment: share of cases not handed off.\n",
        main_table(res) + "\n",
        "## Decisions by label\n", label_table(res) + "\n",
        "## Escalation\n", escalation_table(res) + "\n",
        "## Paired differences (proposed minus baseline, same cases)\n", diff_table(res) + "\n",
        "## Business floor on acting\n",
        f"Acting requires the calibrated confidence to reach max(val threshold, floor). The floor ({fit['act_floor']}) "
        "is a business rule set before any test result and not fitted (see `ml/decision.py`, "
        "`DEFAULT_ACT_FLOOR`). The ablation re-runs each floored system on test with the floor removed.\n",
        report_changes.floor_ablation(res) + "\n",
        _floor_note(res) + "\n",
        "## Country disparity (proposed vs tuned baseline)\n",
        "Group bootstrap 95% CIs per country; the paired difference uses the same cases for both systems.\n",
        report_changes.country_disparity(res) + "\n",
        "## Breakdowns (proposed vs tuned baseline)\n",
    ]
    for key in ("language", "family_split", "family", "country", "country_x_family_split", "segment", "pool_bucket",
                "info_level"):
        parts += [f"### By {key}\n", breakdown_table(res, key, (proposed, "rules_tuned")) + "\n"]
    parts += [
        f"## Selective accuracy vs coverage (`{proposed}`, test)\n",
        "Sweeping the act threshold over the test confidence distribution. The operating point is the val-chosen "
        f"threshold ({res['systems'][proposed]['decider_params']['policy']['t_act']}); this sweep only describes "
        "the trade-off.\n",
        curve_table(res, proposed) + "\n",
        "## Latency and cost\n",
        "\n".join(f"* `{n}`: p50 {b['summary']['latency_ms']['p50']} ms, p95 {b['summary']['latency_ms']['p95']} ms "
                  "per case (parse, rank and decide, in-process, one CPU)" for n, b in res["systems"].items()),
        f"\n{res['cost_assumptions']}\n",
        "## Parser cue recovery (test)\n",
        "Descriptions where the shared parser read back the amount and the date range the text was generated "
        "from. The parser was written from the seen families only.\n",
        parse_table(res) + "\n",
        f"## Error analysis examples (`{proposed}`)\n",
        "Taken automatically from results.json: the first unsafe cases and the first match cases whose top-1 "
        "was wrong.\n",
        examples_block(res, proposed) + "\n",
        "## Previous run\n",
        "Before the number-word and slang parser, the merchant mismatch and generic-word features, and the act "
        "floor. The merchant changes were prompted by error analysis of the previous test run, so part of the "
        "improvement on these test cases is not an independent estimate.\n",
        report_changes.previous_run(res) + "\n",
        "### Parser read-back before and after the number-word and slang change\n",
        "Produced by `ml/parser_readback.py`. Read-back on val was checked after the change; test was measured "
        "once afterwards and not used to choose mappings. F6 amount vocabulary is no longer unseen by the "
        "parser (it now covers slang the generator uses), so the F6 amount gain is not evidence of "
        "generalization.\n",
        report_changes.parser_readback() + "\n",
    ]
    return "\n".join(parts)


def main() -> None:
    out = REPORTS_DIR / "results.md"
    out.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
