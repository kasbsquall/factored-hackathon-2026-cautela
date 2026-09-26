"""Sections of results.md that compare this run with the previous one (all numbers read from JSON)."""

from __future__ import annotations

import json

from ml.data import REPORTS_DIR

PREVIOUS_DIR = REPORTS_DIR / "previous"
READBACK_ROWS = (("val", "val_by_language", "es"), ("val", "val_by_language", "pt"),
                 ("test", "test_by_language_and_family_split", "es/seen"),
                 ("test", "test_by_language_and_family_split", "es/held_out"),
                 ("test", "test_by_language_and_family_split", "pt/seen"),
                 ("test", "test_by_language_and_family_split", "pt/held_out"),
                 ("test", "test_by_family", "F5"), ("test", "test_by_family", "F6"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _pts(d: dict) -> str:
    return f"{100 * d['diff']:+.1f} pts [{100 * d['ci95'][0]:+.1f}, {100 * d['ci95'][1]:+.1f}]"


def _ci(e: dict) -> str:
    return f"{_pct(e['value'])} [{_pct(e['ci95'][0])}, {_pct(e['ci95'][1])}]"


def previous_run(res: dict) -> str:
    prev = _load(PREVIOUS_DIR / "results.json")
    if prev is None:
        return "No previous run snapshot in `ml/reports/previous/`."
    rows = ["| system | top-1 before / now | correct decisions before / now | unsafe before / now | safe automated "
            "resolution before / now |", "|---|---|---|---|---|"]
    for name, body in res["systems"].items():
        s, p = body["summary"], prev["systems"].get(name, {}).get("summary")
        if p is None:
            continue
        rows.append(f"| `{name}` | {_pct(p['top1_accuracy']['value'])} / {_pct(s['top1_accuracy']['value'])} "
                    f"| {_pct(p['correct_decision_rate']['value'])} / {_pct(s['correct_decision_rate']['value'])} "
                    f"| {p['unsafe']['count']} / {s['unsafe']['count']} of {s['unsafe']['denominator']} "
                    f"| {_pct(p['safe_automated_resolution_rate']['value'])} / "
                    f"{_pct(s['safe_automated_resolution_rate']['value'])} |")
    key = f"{prev['proposed_system']}_minus_rules_tuned"
    diffs = ["| metric | previous run | this run |", "|---|---|---|"]
    for metric in ("correct_decision_rate", "unsafe_rate", "correct_decision_rate_held_out", "unsafe_rate_held_out"):
        before, now = prev["paired_differences"][key].get(metric), res["paired_differences"][key].get(metric)
        if before and now:
            diffs.append(f"| {metric} | {_pts(before)} | {_pts(now)} |")
    return ("Same test cases (data version " f"`{prev['data_version']}`), numbers from "
            "`ml/reports/previous/results.json`.\n\n" + "\n".join(rows)
            + "\n\nPaired difference, proposed minus tuned baseline (95% CI):\n\n" + "\n".join(diffs))


def previous_thresholds_below(floor: float) -> list[tuple[str, float]]:
    """Systems whose previous val-tuned act threshold was under ``floor`` (read from previous/fitted.json)."""
    prev = _load(PREVIOUS_DIR / "fitted.json")
    if prev is None:
        return []
    return [(n, s["policy"]["t_act"]) for n, s in prev["systems"].items()
            if "policy" in s and s["policy"]["t_act"] < floor]


def parser_readback() -> str:
    before, after = _load(REPORTS_DIR / "parser_readback_before.json"), _load(REPORTS_DIR / "parser_readback.json")
    if before is None or after is None:
        return "Run `uv run python -m ml.parser_readback` to produce the read-back files."
    rows = [f"| split | slice | amount before (parser `{before['parser_commit']}`) | amount after | date before "
            "| date after |", "|---|---|---|---|---|---|"]
    for split, table, key in READBACK_ROWS:
        b, a = before[table].get(key), after[table].get(key)
        if b and a:
            rows.append(f"| {split} | {key} | {b['amount_read']} / {b['amount_given']} | {a['amount_read']} / "
                        f"{a['amount_given']} | {b['date_read']} / {b['date_given']} | {a['date_read']} / "
                        f"{a['date_given']} |")
    return "\n".join(rows)


def floor_ablation(res: dict) -> str:
    rows = ["| system | val t_act | act floor | unsafe with floor | unsafe without | safe automated resolution with "
            "/ without | difference in unsafe rate | difference in safe automated resolution |",
            "|---|---|---|---|---|---|---|---|"]
    for name, d in res.get("act_floor_ablation", {}).items():
        w, wo, diff = d["with_floor"], d["without_floor"], d["with_minus_without"]
        rows.append(f"| `{name}` | {d['val_t_act']} | {d['act_floor']} | {w['unsafe']['count']} / "
                    f"{w['unsafe']['denominator']} | {wo['unsafe']['count']} / {wo['unsafe']['denominator']} "
                    f"| {_pct(w['safe_automated_resolution_rate']['value'])} / "
                    f"{_pct(wo['safe_automated_resolution_rate']['value'])} | {_pts(diff['unsafe_rate'])} "
                    f"| {_pts(diff['safe_automated_resolution_rate'])} |")
    return "\n".join(rows)


def country_disparity(res: dict) -> str:
    disp = res.get("country_disparity")
    if not disp:
        return "Not computed."
    rows = ["| country | n | metric | tuned baseline | proposed | proposed minus baseline |",
            "|---|---|---|---|---|---|"]
    for country, e in disp["by_country"].items():
        for metric in ("correct_decision_rate", "unsafe_rate", "safe_automated_resolution_rate"):
            c = e[metric]
            rows.append(f"| {country} | {e['n']} | {metric} | {_ci(c['baseline'])} | {_ci(c['proposed'])} "
                        f"| {_pts(c['proposed_minus_baseline'])} |")
    gaps = ["| metric | gap across countries, baseline | gap, proposed |", "|---|---|---|"]
    for metric, g in disp["gap_max_minus_min"].items():
        gaps.append(f"| {metric} | {100 * g['baseline']:.1f} pts | {100 * g['proposed']:.1f} pts |")
    return "\n".join(rows) + "\n\nLargest minus smallest country value:\n\n" + "\n".join(gaps)
