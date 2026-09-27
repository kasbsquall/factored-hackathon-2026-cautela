"""Render eval/report.md: the hand-written eval/report_template.md with every table filled from committed files.

    uv run python -m eval.report_tables      # or `make report`

Sources: eval/results.json (the original evaluation), eval/results_after_fix.json (the rerun of the deployed agent,
with the git commit of each configuration), eval/results_fix_rounds.json (intermediate fix rounds, from saved rows),
eval/llm_extraction.json (what the LLM extraction adds over the parser), ml/reports/results_llm.json and
ml/reports/baselines_{val,test}.json. The tables are regenerated on every run; the prose around them was written
against the committed results. tests/eval/test_report_render.py checks that eval/report.md is exactly this rendering.
"""

from __future__ import annotations

import json
import re
from typing import Any

from eval.paths import EVAL_DIR, REPORT_PATH, RESULTS_PATH, ROOT

ORDER = ("rules", "learned", "llm")
AFTER_ORDER = ("rules", "learned", "llm", "label_rule")
AFTER_FIX_PATH = EVAL_DIR / "results_after_fix.json"
FIX_ROUNDS_PATH = EVAL_DIR / "results_fix_rounds.json"
LLM_EXTRACTION_PATH = EVAL_DIR / "llm_extraction.json"
TEMPLATE_PATH = EVAL_DIR / "report_template.md"
CATS = ("dispute", "recognized", "bad_data", "expired_session", "tool_failure", "multilingual", "human_request",
        "out_of_scope", "unauthorized", "injection", "adversarial", "identity")


def pct(r: dict[str, Any] | None) -> str:
    if not r or r.get("rate") is None:
        return "n/a"
    lo, hi = r["ci95"]
    return f"{r['k']}/{r['n']} = {100 * r['rate']:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"


def short(r: dict[str, Any] | None) -> str:
    if not r or r.get("rate") is None:
        return "n/a"
    return f"{r['k']}/{r['n']} ({100 * r['rate']:.1f}%)"


def headline(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    rows = [("Safe automated resolution (over in-scope)", "safe_automated_resolution"),
            ("  ceiling: in-scope conversations with an automatable gold", "safe_automated_resolution_ceiling"),
            ("  over eligible (gold resolved or recognized)", "safe_automated_resolution_over_eligible"),
            ("  resolved with no clarifying turn", "safe_automated_resolution_first_turn"),
            ("Automation attempted (in-scope)", "automation_attempted"),
            ("Correct outcome (all conversations)", "correct_outcome"),
            ("Containment (all conversations)", "containment"),
            ("Contained but not solved", "contained_but_not_solved"),
            ("Transfers made when the gold is a transfer", "transfer_recall"),
            ("Missed transfers", "missed_transfers"),
            ("Unnecessary transfers", "unnecessary_transfers"),
            ("Correct reason code (among correct transfers made)", "reason_code_correct"),
            ("Handoff passes every rubric item", "handoff_fully_valid"),
            ("Unsafe outcomes (any type)", "unsafe")]
    out = ["| metric | " + " | ".join(f"`{c}`" for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for label, key in rows:
        out.append(f"| {label} | " + " | ".join(pct(res["configs"][c]["summary"][key]) for c in cfgs) + " |")
    lat = lambda c, k, q: res["configs"][c]["latency"][k][q]  # noqa: E731
    out.append("| Latency per call p50 / p95 (ms, in process) | " + " | ".join(
        f"{lat(c, 'per_call_wall_ms', 'p50')} / {lat(c, 'per_call_wall_ms', 'p95')}" for c in cfgs) + " |")
    out.append("| Latency per conversation p50 / p95 (ms) | " + " | ".join(
        f"{lat(c, 'per_conversation_wall_ms', 'p50')} / {lat(c, 'per_conversation_wall_ms', 'p95')}" for c in cfgs)
               + " |")
    out += cost_rows(res, cfgs)
    return "\n".join(out)


COST_ROWS = (("LLM USD per conversation", "usd_per_conversation"),
             ("LLM USD per attempted conversation (in-scope, automation attempted)", "usd_per_attempted_conversation"),
             ("LLM USD per safe automated resolution", "usd_per_safe_automated_resolution"))


def cost_rows(res: dict, cfgs: list[str]) -> list[str]:
    cost = lambda c: res["configs"][c]["cost"]  # noqa: E731
    return [f"| {label} | " + " | ".join(str(cost(c)[key]) for c in cfgs) + " |" for label, key in COST_ROWS]


def unsafe_table(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    types = list(res["configs"][cfgs[0]]["summary"]["unsafe_by_type"])
    out = ["| unsafe event | " + " | ".join(f"`{c}`" for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for t in types:
        out.append(f"| {t} | " + " | ".join(short(res["configs"][c]["summary"]["unsafe_by_type"][t]) for c in cfgs)
                   + " |")
    return "\n".join(out)


def rubric_table(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    items = list(res["configs"][cfgs[0]]["summary"]["handoff_rubric"])
    out = ["| rubric item | " + " | ".join(f"`{c}`" for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for i in items:
        out.append(f"| {i} | " + " | ".join(short(res["configs"][c]["summary"]["handoff_rubric"][i]) for c in cfgs)
                   + " |")
    return "\n".join(out)


def category_table(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    out = ["| category | n | " + " | ".join(f"`{c}` correct (unsafe)" for c in cfgs) + " |",
           "|---|---|" + "---|" * len(cfgs)]
    for cat in CATS:
        n = res["configs"][cfgs[0]]["by_category"].get(cat, {}).get("conversations")
        if not n:
            continue
        cells = []
        for c in cfgs:
            b = res["configs"][c]["by_category"][cat]
            cells.append(f"{b['correct']} ({b['unsafe']})")
        out.append(f"| {cat} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def subcategory_table(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    keys = list(res["configs"][cfgs[0]]["by_subcategory"])
    out = ["| category / subcategory | n | " + " | ".join(f"`{c}`" for c in cfgs) + " |",
           "|---|---|" + "---|" * len(cfgs)]
    for k in keys:
        n = res["configs"][cfgs[0]]["by_subcategory"][k]["conversations"]
        cells = [f"{res['configs'][c]['by_subcategory'][k]['correct']}"
                 + (f" ({res['configs'][c]['by_subcategory'][k]['unsafe']} unsafe)"
                    if res['configs'][c]['by_subcategory'][k]['unsafe'] else "") for c in cfgs]
        out.append(f"| {k} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def outcome_mix(res: dict, cfg: str) -> str:
    out = ["| category | outcomes |", "|---|---|"]
    for cat in CATS:
        b = res["configs"][cfg]["by_category"].get(cat)
        if b:
            out.append(f"| {cat} | " + ", ".join(f"{k} {v}" for k, v in b["outcomes"].items()) + " |")
    return "\n".join(out)


def breakdown_table(res: dict, key: str) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    groups = list(res["configs"][cfgs[0]][key])
    out = ["| group | config | n | correct outcome | safe automated resolution (in-scope) | ceiling | missed transfers "
           "| unsafe |", "|---|---|---|---|---|---|---|---|"]
    for g in groups:
        for c in cfgs:
            b = res["configs"][c][key][g]
            out.append(f"| {g} | `{c}` | {b['conversations']} | {pct(b['correct_outcome'])} | "
                       f"{pct(b['safe_automated_resolution'])} | {short(b['safe_automated_resolution_ceiling'])} | "
                       f"{short(b['missed_transfers'])} | {short(b['unsafe'])} |")
    return "\n".join(out)


def comparisons(res: dict) -> str:
    out = ["| comparison | metric | difference (points) | 95% CI | conversations (groups) |", "|---|---|---|---|---|"]
    for name, metrics in (res.get("comparisons") or {}).items():
        for m, d in metrics.items():
            if d["diff"] is None:
                continue
            lo, hi = d["ci95"]
            out.append(f"| {name} | {m} | {100 * d['diff']:+.1f} | [{100 * lo:+.1f}, {100 * hi:+.1f}] | "
                       f"{d['n']} ({d['groups']}) |")
    return "\n".join(out)


def attentive(res: dict) -> str:
    out = ["| config | customer | correct outcome | safe automated resolution | ceiling | unsafe | wrong_charge_write |",
           "|---|---|---|---|---|---|---|"]
    for c in ("rules", "learned"):
        body = res["configs"].get(c)
        if not body or "attentive_customer" not in body:
            continue
        for who, s in (("compliant", body["summary"]), ("attentive", body["attentive_customer"]["summary"])):
            ceiling = body["summary"]["safe_automated_resolution_ceiling"]  # same suite and gold for both customers
            out.append(f"| `{c}` | {who} | {short(s['correct_outcome'])} | {short(s['safe_automated_resolution'])} | "
                       f"{short(ceiling)} | {short(s['unsafe'])} | {short(s['unsafe_by_type']['wrong_charge_write'])} |")
    return "\n".join(out)


def variance_table(res: dict) -> str:
    v = res["configs"].get("llm", {}).get("variance")
    if not v:
        return "(no repeated runs)"
    out = ["| run | conversations | correct outcome | safe automated resolution (in-scope of the subset) | unsafe | "
           "containment | USD |", "|---|---|---|---|---|---|---|"]
    for r in v["runs"]:
        out.append(f"| {r['run']} | {r['conversations']} | {short(r['correct_outcome'])} | "
                   f"{short(r['safe_automated_resolution'])} | {short(r['unsafe'])} | {short(r['containment'])} | "
                   f"{r['cost_usd']} |")
    sp = v["spread"]
    out.append("")
    out.append("| metric | mean | sd | min | max |")
    out.append("|---|---|---|---|---|")
    for k, s in sp.items():
        out.append(f"| {k} | {s['mean']} | {s['sd']} | {s['min']} | {s['max']} |")
    same = v["same_outcome_all_runs"]
    out.append("")
    out.append(f"Same final outcome (kind and reason code) in all runs: {same['k']} of {same['n']} conversations.")
    return "\n".join(out)


def failures(res: dict, cfg: str, n: int = 14) -> str:
    out = ["| conversation | category / sub | gold | got | why | trace ids (last two calls) |",
           "|---|---|---|---|---|---|"]
    for f in res["configs"][cfg]["failure_examples"][:n]:
        gold = f["gold_kind"] + (":" + "|".join(f["gold_reasons"]) if f["gold_reasons"] else "")
        got = f["outcome"] + (":" + f["handoff_code"] if f["handoff_code"] else "")
        out.append(f"| `{f['conv_id']}` | {f['category']} / {f['subcategory']} | {gold} | {got} | "
                   f"{f['why_incorrect']} | {', '.join(f'`{t}`' for t in f['trace_ids'])} |")
    return "\n".join(out)


def pool_tables(res: dict) -> str:
    cfgs = [c for c in ORDER if c in res["configs"]]
    buckets = list(res["configs"][cfgs[0]].get("by_pool_bucket_in_scope", {}))
    out = ["| pool bucket (in-scope) | config | n | correct outcome | safe automated resolution | ceiling | unsafe |",
           "|---|---|---|---|---|---|---|"]
    for b in buckets:
        for c in cfgs:
            g = res["configs"][c]["by_pool_bucket_in_scope"][b]
            out.append(f"| {b} | `{c}` | {g['conversations']} | {pct(g['correct_outcome'])} | "
                       f"{pct(g['safe_automated_resolution'])} | {short(g['safe_automated_resolution_ceiling'])} | "
                       f"{short(g['unsafe'])} |")
    suite = res.get("suite_pool_distribution")
    real = res.get("real_pool_distribution")
    if suite and real:
        out += ["", "| pool bucket | suite source cases | real: all transactions, 90 days | real: approved or pending "
                "debits, 90 days | real: all transactions, 120 days | real: approved or pending debits, 120 days |",
                "|---|---|---|---|---|---|"]
        w90, w120 = real["windows"]["90d"], real["windows"]["120d"]
        for b in suite["count"]:
            cells = [f"{100 * w[k]['share'][b]:.1f}%" for w in (w90, w120)
                     for k in ("all_transactions", "approved_or_pending_debits")]
            out.append(f"| {b} | {suite['count'][b]} of {suite['cases']} | " + " | ".join(cells) + " |")
        n = w90["all_transactions"]["snapshots"]
        out.append(f"| median / p90 | 4 / 6 | {w90['all_transactions']['median']} / {w90['all_transactions']['p90']} | "
                   f"{w90['approved_or_pending_debits']['median']} / {w90['approved_or_pending_debits']['p90']} | "
                   f"{w120['all_transactions']['median']} / {w120['all_transactions']['p90']} | "
                   f"{w120['approved_or_pending_debits']['median']} / {w120['approved_or_pending_debits']['p90']} |")
        out.append("")
        out.append(f"Real-data columns: {real['population']}, {n} customer snapshots, from {real['source']}.")
    return "\n".join(out)


def security_table(res: dict) -> str:
    sec = res["configs"].get("llm", {}).get("security_repeats")
    if not sec:
        return "(security repeats not run)"
    out = ["| run | injection correct | injection unsafe | unauthorized correct | unauthorized unsafe | USD |",
           "|---|---|---|---|---|---|"]
    for r in sec["runs"]:
        i, u = r["injection"], r["unauthorized"]
        out.append(f"| {r['run']} | {i['correct']}/{i['conversations']} | {i['unsafe']}/{i['conversations']} | "
                   f"{u['correct']}/{u['conversations']} | {u['unsafe']}/{u['conversations']} | {r['cost_usd']} |")
    same = sec["same_outcome_all_runs"]
    out += ["", f"Same final outcome in all runs: {same['k']} of {same['n']} conversations. Calls refused by the "
            f"cap: {sec['refused_calls']}."]
    return "\n".join(out)


def ml_pool(res_llm: dict | None) -> str:
    if not res_llm or "by_pool_bucket" not in res_llm:
        return "(not generated)"
    out = ["| pool bucket | cases | rung | top-1 on match | correct decisions | unsafe | safe act rate (component) |",
           "|---|---|---|---|---|---|---|"]
    for b, v in res_llm["by_pool_bucket"].items():
        for n, s in v["systems"].items():
            out.append(f"| {b} | {v['cases']} | `{n}` | {100 * s['top1_accuracy']:.1f}% | "
                       f"{100 * s['correct_decision_rate']:.1f}% | {s['unsafe_count']} | "
                       f"{100 * s['safe_automated_resolution_rate']:.1f}% |")
    out += ["", "| pool bucket | comparison | metric | points | 95% CI |", "|---|---|---|---|---|"]
    for b, v in res_llm["by_pool_bucket"].items():
        for comp, metrics in v["paired"].items():
            for m, d in metrics.items():
                if d["diff"] != d["diff"]:
                    continue
                lo, hi = d["ci95"]
                out.append(f"| {b} | {comp} | {m} | {100 * d['diff']:+.1f} | [{100 * lo:+.1f}, {100 * hi:+.1f}] |")
    return "\n".join(out)


def ml_rung() -> str:
    path = ROOT / "ml" / "reports" / "results_llm.json"
    if not path.exists():
        return "(ml/reports/results_llm.json not generated)"
    r = json.loads(path.read_text(encoding="utf-8"))
    s, base, v = r["run1_summary"], r["baselines_same_cases"], r["variance"]
    n = s["n_cases"]
    out = ["| rung (original test split, same cases) | top-1 on match | correct decisions | unsafe | "
           "safe act rate (component; ceiling = share of match cases) |", "|---|---|---|---|---|"]
    for name, b in base.items():
        out.append(f"| `{name}` | {100 * b['top1_accuracy']:.1f}% | {100 * b['correct_decision_rate']:.1f}% | "
                   f"{b['unsafe_count']}/{n} | {100 * b['safe_automated_resolution_rate']:.1f}% |")
    runs = v["per_run"]
    fmt = lambda k: " / ".join(f"{100 * x:.1f}%" for x in runs[k])  # noqa: E731
    unsafe_counts = " / ".join(str(round(x * n)) for x in runs["unsafe_rate"])
    out.append(f"| `llm_ranker_calibrated` (runs 1 / 2 / 3) | {fmt('top1_accuracy')} | {fmt('correct_decision_rate')} "
               f"| {unsafe_counts} of {n} | {fmt('safe_automated_resolution_rate')} |")
    out += ["", "| paired difference, LLM run 1 minus | metric | points | 95% CI |", "|---|---|---|---|"]
    for comp, metrics in r["paired_run1"].items():
        for m, d in metrics.items():
            lo, hi = d["ci95"]
            out.append(f"| {comp.removeprefix('llm_minus_')} | {m} | {100 * d['diff']:+.1f} | "
                       f"[{100 * lo:+.1f}, {100 * hi:+.1f}] |")
    same = v["same_decision_all_runs"]
    out += ["", f"Same decision and same top charge in all runs: {same['count']} of {same['denominator']} cases. "
            f"Provider errors per run: {', '.join(str(m['provider_errors'] or 0) for m in r['runs'])}. "
            f"Spend for this rung: {r['spend']['this_script_usd']} USD (fit {r['fit']['cost_usd']} USD), "
            f"calls refused by the cap: {r['spend']['refused_calls']}."]
    return "\n".join(out)


AFTER_ROWS = (("Safe automated resolution (in-scope)", "safe_automated_resolution"),
              ("  ceiling: in-scope conversations with an automatable gold", "safe_automated_resolution_ceiling"),
              ("  over eligible (gold resolved or recognized)", "safe_automated_resolution_over_eligible"),
              ("Correct outcome", "correct_outcome"), ("Unsafe outcomes", "unsafe"), ("Containment", "containment"),
              ("Automation attempted (in-scope)", "automation_attempted"), ("Missed transfers", "missed_transfers"),
              ("Unnecessary transfers", "unnecessary_transfers"),
              ("Correct reason code (among correct transfers)", "reason_code_correct"),
              ("Handoff passes every rubric item", "handoff_fully_valid"))


def _columns(before: dict, after: dict) -> list[tuple[str, str, dict]]:
    cols = []
    for c in AFTER_ORDER:
        if c in before["configs"]:
            cols.append((f"`{c}` before", c, before["configs"][c]))
        if c in after["configs"]:
            cols.append((f"`{c}` after", c, after["configs"][c]))
    return cols


def after_headline(before: dict, after: dict) -> str:
    """Before (eval/results.json) and after (eval/results_after_fix.json) for every configuration."""
    cols = _columns(before, after)
    out = ["| metric | " + " | ".join(h for h, _, _ in cols) + " |", "|---|" + "---|" * len(cols)]
    for label, key in AFTER_ROWS:
        out.append(f"| {label} | " + " | ".join(short(b["summary"][key]) for _, _, b in cols) + " |")
    lat = lambda b, k: f"{b['latency'][k]['p50']} / {b['latency'][k]['p95']}"  # noqa: E731
    out.append("| Latency per call p50 / p95 (ms) | " + " | ".join(lat(b, "per_call_wall_ms") for _, _, b in cols) + " |")
    out.append("| Latency per conversation p50 / p95 (ms) | "
               + " | ".join(lat(b, "per_conversation_wall_ms") for _, _, b in cols) + " |")
    out.append("| LLM calls / input tokens / output tokens | " + " | ".join(
        f"{b['cost']['llm_calls']} / {b['cost']['input_tokens']} / {b['cost']['output_tokens']}" for _, _, b in cols)
               + " |")
    out.append("| LLM USD total | " + " | ".join(str(b["cost"]["llm_cost_usd_total"]) for _, _, b in cols) + " |")
    for label, key in COST_ROWS:
        out.append(f"| {label} | " + " | ".join(str(b["cost"][key]) for _, _, b in cols) + " |")
    return "\n".join(out)


def after_category(before: dict, after: dict) -> str:
    cols = _columns(before, after)
    first = cols[0][2]["by_category"]
    out = ["| category | n | " + " | ".join(h for h, _, _ in cols) + " |", "|---|---|" + "---|" * len(cols)]
    for cat in sorted(first):
        cells = []
        for _, _, b in cols:
            x = b["by_category"][cat]
            cells.append(f"{x['correct']}" + (f" ({x['unsafe']})" if x["unsafe"] else ""))
        out.append(f"| {cat} | {first[cat]['conversations']} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def after_subcategory(before: dict, after: dict) -> str:
    cols = _columns(before, after)
    keys = list(cols[0][2]["by_subcategory"])
    out = ["| category / subcategory | n | " + " | ".join(h for h, _, _ in cols) + " |",
           "|---|---|" + "---|" * len(cols)]
    for k in keys:
        cells = []
        for _, _, b in cols:
            x = b["by_subcategory"][k]
            cells.append(f"{x['correct']}" + (f" ({x['unsafe']})" if x["unsafe"] else ""))
        out.append(f"| {k} | {cols[0][2]['by_subcategory'][k]['conversations']} | " + " | ".join(cells) + " |")
    return "
".join(out)


def after_llm_fallbacks(after: dict) -> str:
    b = after["configs"].get("llm")
    if not b:
        return "(llm configuration not in eval/results_after_fix.json)"
    fb = b["llm_fallbacks"]
    sources = fb["reply_sources"]
    total = sum(sources.values())
    out = ["| item (`llm` after fixes) | count |", "|---|---|",
           f"| LLM calls that failed and fell back to the parser or a template | {fb['llm_failed_calls']} |"]
    out += [f"| replies worded by {k} | {v} of {total} |" for k, v in sorted(sources.items())]
    out += [f"| reply note {k} | {v} |" for k, v in sorted(fb["reply_notes"].items())]
    out += [f"| understand step source {k} | {v} |" for k, v in sorted(fb["understand_sources"].items())]
    return "
".join(out)


def after_provenance(after: dict) -> str:
    out = ["| config | run id | git commit | uncommitted code paths at start | disposition model (loaded from) | wall s |",
           "|---|---|---|---|---|---|"]
    for c in AFTER_ORDER:
        b = after["configs"].get(c)
        if not b:
            continue
        code = b.get("code", {})
        dirty = ", ".join(f"`{p}`" for p in code.get("uncommitted_paths", [])) or "none"
        out.append(f"| `{c}` | `{b['run_id']}` | `{code.get('git_commit', 'unknown')[:7]}` | {dirty} | "
                   f"`{b.get('disposition_model')}` ({b.get('disposition_source')}) | {b['wall_s']} |")
    spend = after.get("spend", {})
    out += ["", f"Suite sha256 `{after['suite_sha256'][:8]}…`, {after['suite_conversations']} conversations. LLM spend "
            f"ledger `{spend.get('ledger')}`: {spend.get('calls')} calls, USD {spend.get('usd_estimated')} of a "
            f"USD {spend.get('cap_usd')} cap, {spend.get('refused_calls')} calls refused by the cap."]
    return "\n".join(out)


def fix_rounds(fr: dict) -> str:
    rounds = fr["rounds"]
    labels = list(rounds)
    cols = [(f"`{c}` {lab.replace('_', ' ')}", rounds[lab]["configs"][c]) for c in ("rules", "learned")
            for lab in labels if c in rounds[lab]["configs"]]
    out = ["| metric | " + " | ".join(h for h, _ in cols) + " |", "|---|" + "---|" * len(cols)]
    for label, key in AFTER_ROWS:
        out.append(f"| {label} | " + " | ".join(short(b["summary"][key]) for _, b in cols) + " |")
    out += ["", "Run ids: " + "; ".join(f"{lab.replace('_', ' ')}: " + ", ".join(
        f"`{b['run_id']}`" for b in rounds[lab]["configs"].values()) for lab in labels) + "."]
    return "\n".join(out)


def spend_ledgers(fr: dict) -> str:
    out = ["| ledger (git-ignored, counters copied to eval/results_fix_rounds.json) | cap USD | calls | calls with no "
           "price | calls refused by the cap | USD recorded |", "|---|---|---|---|---|---|"]
    for name, led in fr.get("spend_ledgers", {}).items():
        out.append(f"| `data/eval/{name}` | {led['cap_usd']:.2f} | {led['calls']} | {led['unpriced_calls']} | "
                   f"{led['refused']} | {led['usd']:.4f} |")
    return "
".join(out)


def llm_extraction(x: dict | None) -> str:
    if not x:
        return "(eval/llm_extraction.json not generated)"
    e, first = x["extraction"], x["extraction"]["first_dispute_message"]
    v = e["values"]
    out = ["| what the model supplied that the parser did not read (all model readings) | count |", "|---|---|",
           f"| messages read by the model | {e['model_readings']} |"]
    for field in ("amount", "date", "merchant"):
        out.append(f"| {field}: read by the parser / supplied only by the model | {v.get(f'parser_{field}', 0)} / "
                   f"{v.get(f'model_only_{field}', 0)} |")
    for change, n in e["intent_changes"].items():
        out.append(f"| intent, parser -> merged reading: {change} | {n} |")
    out += ["", "| first message of a dispute conversation, against the hints it was written from | count |",
            "|---|---|", f"| messages | {first.get('first_dispute_messages', 0)} |"]
    for field in ("amount", "date"):
        out.append(f"| {field} in the hints | {first.get(f'{field}_in_hints', 0)} |")
        for kind in ("parser", "model_added"):
            for verdict in ("matches_hint", "wrong_or_not_in_hints"):
                out.append(f"| {field} {kind.replace('_', ' ')}, {verdict.replace('_', ' ')} | "
                           f"{first.get(f'{kind}_{field}_{verdict}', 0)} |")
        out.append(f"| {field} in the hints, read by neither | {first.get(f'{field}_in_hints_read_by_neither', 0)} |")
    return "\n".join(out)


def component_label_rule() -> str:
    """ml/reports/baselines_{val,test}.json: the label rule on parsed text next to the learned decisions."""
    files = {sp: ROOT / "ml" / "reports" / f"baselines_{sp}.json" for sp in ("val", "test")}
    if not all(p.exists() for p in files.values()):
        return "(ml/reports/baselines_*.json not generated)"
    data = {sp: json.loads(p.read_text(encoding="utf-8")) for sp, p in files.items()}
    out = ["| system (component) | split | correct decisions | unsafe | safe act rate (ceiling: match cases) | ECE |",
           "|---|---|---|---|---|---|"]
    for name in data["test"]["systems"]:
        for sp in ("val", "test"):
            s = data[sp]["systems"][name]["summary"]
            c = s["safe_automated_resolution_ceiling"]
            out.append(f"| `{name}` | {sp} | {100 * s['correct_decision_rate']['value']:.1f}% | "
                       f"{s['unsafe']['count']}/{s['n_cases']} | {100 * s['safe_automated_resolution_rate']['value']:.1f}% "
                       f"({c['count']}/{c['denominator']}) | {s['ece']['value']} |")
    return "\n".join(out)


def render(template: str, parts: dict[str, str]) -> str:
    for name, text in parts.items():
        template = template.replace(f"<!-- table:{name} -->", text)
    return template


def _load(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_parts() -> dict[str, str]:
    res = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    parts = {"headline": headline(res), "unsafe": unsafe_table(res), "rubric": rubric_table(res),
             "category": category_table(res), "subcategory": subcategory_table(res),
             "language": breakdown_table(res, "by_language"), "language_in_scope": breakdown_table(res, "by_language_in_scope"),
             "country": breakdown_table(res, "by_country"), "segment": breakdown_table(res, "by_segment"),
             "comparisons": comparisons(res), "attentive": attentive(res), "variance": variance_table(res)}
    for cfg in res["configs"]:
        parts[f"failures_{cfg}"] = failures(res, cfg)
        parts[f"outcomes_{cfg}"] = outcome_mix(res, cfg)
    parts["ml_rung"] = ml_rung()
    parts["pool"] = pool_tables(res)
    parts["security"] = security_table(res)
    llm_path = ROOT / "ml" / "reports" / "results_llm.json"
    parts["ml_pool"] = ml_pool(json.loads(llm_path.read_text(encoding="utf-8")) if llm_path.exists() else None)
    after = json.loads(AFTER_FIX_PATH.read_text(encoding="utf-8"))
    parts |= {"after_headline": after_headline(res, after), "after_category": after_category(res, after),
              "after_provenance": after_provenance(after), "after_comparisons": comparisons(after),
              "after_subcategory": after_subcategory(res, after), "after_llm_fallbacks": after_llm_fallbacks(after),
              "fix_rounds": fix_rounds(json.loads(FIX_ROUNDS_PATH.read_text(encoding="utf-8"))),
              "spend_ledgers": spend_ledgers(json.loads(FIX_ROUNDS_PATH.read_text(encoding="utf-8"))),
              "llm_extraction": llm_extraction(_load(LLM_EXTRACTION_PATH)),
              "component_label_rule": component_label_rule()}
    return parts


def render_report() -> str:
    """eval/report.md as it must read: the template with every marker replaced from the committed files."""
    body = render(TEMPLATE_PATH.read_text(encoding="utf-8"), build_parts())
    missing = sorted(set(re.findall(r"<!-- table:([a-z_]+) -->", body)))
    if missing:
        raise SystemExit(f"template markers without a table: {missing}")
    return body


def main() -> None:
    REPORT_PATH.write_text(render_report(), encoding="utf-8", newline="\n")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
