"""The headline section of results.md: the fresh test split, evaluated once (all numbers read from JSON).

``ml/reports/results_fresh.json`` is written by ``ml.evaluate --split test_fresh``. The protocol facts quoted here
come from the ``test_fresh`` section of ``eval/cases/disputes/manifest.json``.
"""

from __future__ import annotations

import json

from ml.data import CASES_DIR, REPORTS_DIR

FRESH_FILE = REPORTS_DIR / "results_fresh.json"
GROUP_TITLES = {"language": "By language", "country": "By country", "family_split": "Seen or held-out family",
                "family": "By family (F7 is the family written for this split)"}


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(entry: dict) -> str:
    if entry.get("value") is None:
        return "n/a"
    ci = entry.get("ci95")
    return f"{_pct(entry['value'])} [{_pct(ci[0])}, {_pct(ci[1])}]" if ci else _pct(entry["value"])


def _pts(d: dict) -> str:
    return f"{100 * d['diff']:+.1f} [{100 * d['ci95'][0]:+.1f}, {100 * d['ci95'][1]:+.1f}]"


def _unsafe(u: dict) -> str:
    return f"{u['count']} / {u['denominator']} (acted {u['acted_denominator']})"


def headline_table(res: dict) -> str:
    rows = ["| system | correct decisions | unsafe (count / cases, acted) | unsafe rate | safe act rate "
            "| top-1 (match) | automation attempted |", "|---|---|---|---|---|---|---|"]
    for name, body in res["systems"].items():
        s = body["summary"]
        rows.append(f"| `{name}` | {_ci(s['correct_decision_rate'])} | {_unsafe(s['unsafe'])} | {_ci(s['unsafe_rate'])} "
                    f"| {_ci(s['safe_automated_resolution_rate'])} | {_ci(s['top1_accuracy'])} "
                    f"| {_pct(s['automation_attempted_share'])} |")
    return "\n".join(rows)


def overall_diffs(res: dict, comp: str) -> str:
    rows = ["| metric | difference, points [95% CI] | cases |", "|---|---|---|"]
    for metric, d in res["paired_differences"][comp].items():
        rows.append(f"| {metric} | {_pts(d)} | {d['n_cases']} |")
    return "\n".join(rows)


def group_table(res: dict, key: str) -> str:
    rows = [f"| {key} | cases (bootstrap groups) | unsafe, baseline | unsafe, proposed | correct, baseline / proposed "
            "| correct diff | unsafe diff | safe act rate diff |", "|---|---|---|---|---|---|---|---|"]
    for value, g in res["paired_by_group"][key].items():
        d, r = g["proposed_minus_baseline"], g["rates"]
        rows.append(f"| {value} | {g['n']} ({g['n_groups']}) | {_unsafe(g['unsafe']['baseline'])} "
                    f"| {_unsafe(g['unsafe']['proposed'])} | {_pct(r['baseline']['correct_decision_rate'])} / "
                    f"{_pct(r['proposed']['correct_decision_rate'])} | {_pts(d['correct_decision_rate'])} "
                    f"| {_pts(d['unsafe_rate'])} | {_pts(d['safe_automated_resolution_rate'])} |")
    return "\n".join(rows)


def versus_old_test(fresh: dict, old: dict) -> str:
    rows = ["| system | correct: test / test_fresh | unsafe: test / test_fresh | safe act rate: test / test_fresh |",
            "|---|---|---|---|"]
    for name, body in fresh["systems"].items():
        f, o = body["summary"], old["systems"].get(name, {}).get("summary")
        if o is None:
            continue
        rows.append(f"| `{name}` | {_pct(o['correct_decision_rate']['value'])} / "
                    f"{_pct(f['correct_decision_rate']['value'])} | {o['unsafe']['count']} of "
                    f"{o['unsafe']['denominator']} / {f['unsafe']['count']} of {f['unsafe']['denominator']} "
                    f"| {_pct(o['safe_automated_resolution_rate']['value'])} / "
                    f"{_pct(f['safe_automated_resolution_rate']['value'])} |")
    return "\n".join(rows)


def protocol(res: dict) -> str:
    man = json.loads((CASES_DIR / "manifest.json").read_text(encoding="utf-8"))["test_fresh"]
    cfg, s = man["config"], res["systems"][res["proposed_system"]]["summary"]
    return "\n".join([
        f"* Cases: {s['n_cases']} ({s['n_groups']} Spanish sources plus their team-generated Portuguese "
        f"renderings). Labels: {s['n_by_label']}. File sha256 `{man['file_sha256'][:16]}` frozen in the manifest "
        "before any model ran on it; `ml.scenarios.build --verify` rebuilds and checks it.",
        f"* Generation seed {cfg['seed']} (the original build used {cfg['bucket_seed']}). Customers: the original "
        f"test bucket minus every customer the original build loaded ({man['customers_eligible']} eligible), so no "
        "customer or transaction of train, val or test appears (checked by `tests/ml_tests/test_committed_cases.py`).",
        f"* Report dates {cfg['period'][0]} to {cfg['period'][1]}, the same window as test: the organizer "
        "transactions end on 2026-06-18, so a later window does not exist.",
        f"* Families: F7 ({cfg['fresh_family_share']:.0%} of Spanish sources), a messaging-app register with MX, CO "
        "and AR variants and a pt-BR rendering, written for this split after training and before any model output "
        f"on it (`ml/scenarios/render_fresh.py`); F5 and F6 {cfg['heldout_share']:.0%}; F1 to F4 the rest. "
        f"Portuguese share {cfg['pt_share']:.0%}.",
        "* Evaluated exactly once with the fitted artifacts of `fitted.json` (data version "
        f"`{res['data_version']}`): no refit, no threshold change, no fix after reading the results. "
        "`ml.evaluate --split test_fresh` refuses to run again once `results_fresh.json` exists.",
    ])


def section() -> str:
    if not FRESH_FILE.exists():
        return ("## Headline: fresh test split\n\n`ml/reports/results_fresh.json` is missing; run "
                "`uv run python -m ml.evaluate --split test_fresh` once.\n")
    res = json.loads(FRESH_FILE.read_text(encoding="utf-8"))
    old = json.loads((REPORTS_DIR / "results.json").read_text(encoding="utf-8"))
    comp = f"{res['proposed_system']}_minus_rules_tuned"
    parts = ["## Headline: fresh test split (`test_fresh`, evaluated once)\n",
             "This is the primary result. The original test split below was used for error analysis, so its "
             "numbers are optimistic.\n", protocol(res) + "\n", headline_table(res) + "\n",
             f"Paired differences on the same cases (`{comp}`, bootstrap over Spanish source groups; the "
             "comparison with `rules_fixed` is in results_fresh.json):\n", overall_diffs(res, comp) + "\n"]
    for key, title in GROUP_TITLES.items():
        parts += [f"### {title} (`{comp}`)\n", group_table(res, key) + "\n"]
    parts += ["### Original test split against the fresh split\n", versus_old_test(res, old) + "\n"]
    return "\n".join(parts)
