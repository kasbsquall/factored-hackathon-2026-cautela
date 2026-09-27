"""Render why-this-workflow.md from the collected evidence. Every number printed here comes from `ev`, which
`run.collect` filled from warehouse queries, eval/results.json and ml/reports/results.json; nothing is typed in by
hand."""

from __future__ import annotations

from data_analytics import render_decisions as dec
from data_analytics.cost_model import CENTRAL_HOURLY_COST_USD
from data_analytics.fmt import n, pct, rate, table, usd, usd_llm, usd_year

WEEKDAYS = dec.WEEKDAYS


def _central(ev: dict, rate_name: str, widest: bool) -> dict | None:
    rows = [p for p in ev["cost_model"]["projections"] if p["scenario"].startswith("central")
            and p["rate"] == rate_name]
    if not rows:
        return None
    return max(rows, key=lambda p: p["reach_share"]) if widest else min(rows, key=lambda p: p["reach_share"])


def _headline(ev: dict) -> str:
    d, w, rk = ev["dispute_profile"], ev["workflows"], ev["workflow_ranking"]
    top = next((r for r in w if r["is_unrecognized_charge"]), None)
    lines = []
    chosen = next((r for r in rk["rows"] if r["verifiable"] == "yes"), None)
    if chosen:
        lead = rk["rows"][0]
        lines.append(
            f"* Why this workflow: disputes rank {chosen['rank']} of {len(rk['rows'])} candidate workflows by "
            f"agent-hours on contacts not resolved first time ({n(chosen['unresolved_contact_hours_per_year'])} "
            f"agent-hours per year against {n(lead['unresolved_contact_hours_per_year'])} for {lead['proxy']}). They "
            "were chosen because each outcome can be verified against the customer's own transactions, which makes "
            "automating them checkable and safe to measure (section 1).")
    if top:
        runner = next((r for r in w if r is not top), None)
        gap = (f", {n(top['complaints'] - runner['complaints'])} complaints ahead of the next type"
               if runner and top["volume_rank"] == 1 else "")
        lines.append(
            f"* Unrecognized-charge complaints (\"Cargo no reconocido\"): {n(d['complaints'])} of "
            f"{n(top['all_complaints'])} complaints over {n(d['window_days'])} days, "
            f"{rate(top['share_of_complaints'])}, volume rank {top['volume_rank']} of {len(w)} complaint types{gap} "
            "(section 2).")
    lines.append(
        f"* SLA breached: {pct(d['sla_breached'], d['complaints'])}. Resolution time p50 "
        f"{n(d['resolution_days_p50'])} days, p90 {n(d['resolution_days_p90'])} days (n = "
        f"{n(d['with_resolution_days'])}). Reception: {rate(ev['channel_reach']['call_center_share'])} by call "
        f"center, {rate(ev['channel_reach']['chat_eligible_share'])} by App or Web (section 4).")
    cpr = ev["cost_model"]["cost_per_resolution"]
    if cpr["human_usd_per_dispute_by_hourly_cost"]:
        lines.append(
            f"* Cost per resolution: a human dispute intake costs "
            f"{usd(cpr['human_usd_per_dispute_by_hourly_cost'][str(CENTRAL_HOURLY_COST_USD)])} at a placeholder "
            f"USD {CENTRAL_HOURLY_COST_USD:g} per agent-hour ({n(cpr['human_minutes_per_dispute'])} minutes); a safe "
            f"automated resolution costs {usd_llm(cpr['llm_usd_per_safe_automated_resolution'])} of LLM tokens, "
            f"measured in `{cpr['source_llm']}` (hosting not measured).")
    lo, hi = _central(ev, "conservative", False), _central(ev, "optimistic", True)
    if lo and hi:
        m_lo = next(m for m in lo["money"] if m["hourly_cost_usd"] == CENTRAL_HOURLY_COST_USD)
        m_hi = next(m for m in hi["money"] if m["hourly_cost_usd"] == CENTRAL_HOURLY_COST_USD)
        lines.append(
            f"* Savings range, central handling time: {n(lo['hours_saved_per_year'])} agent-hours per year "
            f"({usd_year(m_lo['net_savings_per_year_usd'])} per year) with the deployed configuration's end-to-end "
            f"rate of {rate(lo['safe_automated_resolution_rate'])} on App and Web disputes only, up to "
            f"{n(hi['hours_saved_per_year'])} agent-hours per year ({usd_year(m_hi['net_savings_per_year_usd'])}) "
            f"with the component rate of {rate(hi['safe_automated_resolution_rate'])} and call-center disputes "
            "routed to the flow (an assumption). Failed attempts are charged at full human cost plus tokens. "
            "These are offline projections; no saving was measured in production (section 9).")
    hq = ev["handoff_queue"]["scenarios"]
    if hq:
        worst = max(hq, key=lambda s: s["agent_minutes_peak_hour"])
        lines.append(
            f"* Handoff queue: at most {worst['handoffs_per_day_p95']:.1f} handoffs on a p95 day and "
            f"{n(worst['agent_minutes_peak_hour'])} agent-minutes in the busiest weekday-hour"
            + (", a fraction of one agent, so the existing complaint queue can absorb them"
               if worst["agent_minutes_peak_hour"] < 60 else "") + " (section 5).")
    th = ev["thresholds"]
    o = th["amount"]["overall"]
    heavy = sorted(th["amount"]["by_type"], key=lambda r: -r["at_or_above"])[:2]
    lines.append(
        f"* Policy thresholds over the whole window: USD {th['amount']['threshold_usd']:g} sends "
        f"{pct(o['at_or_above'], o['charges'])} of disputable charges to a person, "
        f"{rate(sum(r['at_or_above'] for r in heavy) / o['at_or_above'] if o['at_or_above'] else None)} of them "
        f"{' and '.join(r['transaction_type'].lower() + 's' for r in heavy)}; the fraud rule "
        f"sends {rate(th['fraud']['disputable']['share_routed'], 3)}"
        + (", exactly the flagged charges" if th["fraud"]["disputable"]["routed_by_score_only"] == 0 else "")
        + " (section 7).")
    res = {r["was_resolved"]: r for r in ev["satisfaction"]["surveys"]["by_first_contact_resolution"]}
    if True in res and False in res:
        lines.append(f"* Satisfaction: CSAT averages {res[True]['mean_score']:.2f} when a contact is resolved first "
                     f"time and {res[False]['mean_score']:.2f} when it is not; surveys cannot be linked to disputes "
                     "(section 8).")
    return "\n".join(lines)


def _workflow_section(ev: dict) -> str:
    w = ev["workflows"]
    body = [[r["volume_rank"], r["complaint_type"], n(r["complaints"]),
             f"{rate(r['share_of_complaints'])} [{rate(r['share_ci95'][0])}, {rate(r['share_ci95'][1])}]",
             pct(r["sla_breached"], r["complaints"]), pct(r["escalated"], r["complaints"]),
             f"{n(r['resolution_days_p50'])} / {n(r['resolution_days_p90'])} (n = {n(r['with_resolution_days'])})",
             pct(r["next_complaint_30d"], r["complaints"])] for r in w]
    text = [table(["rank", "complaint type", "complaints", "share of all complaints [95% CI]", "SLA breached",
                   "escalated", "resolution days p50 / p90", "another complaint within 30 days"], body)]
    if len(w) >= 2 and w[0]["share_ci95"] and w[1]["share_ci95"]:
        overlap = w[0]["share_ci95"][0] <= w[1]["share_ci95"][1]
        text.append(
            f"\nThe first two types differ by {n(w[0]['complaints'] - w[1]['complaints'])} complaints. "
            + ("Their share intervals overlap (the intervals treat the three years as a sample of the bank's "
               "complaint process), so the volume ranking alone does not separate them."
               if overlap else "Their share intervals do not overlap."))
    return "\n".join(text)


def _fcr_caveat(ev: dict) -> str:
    cats = {r["reason_category"]: r for r in ev["fcr_by_reason_category"]}
    c, t = cats.get("Complaint"), cats.get("Transactional")
    if not (c and t):
        return ""
    return (f"\n\nWhich reason a disputed-charge call is filed under is not recorded. If it is Complaint, its "
            f"first-contact resolution is {pct(c['resolved_first_contact'], c['fcr_denominator'])}; if it is "
            f"Transactional, {pct(t['resolved_first_contact'], t['fcr_denominator'])}. The low figure is evidence "
            "about complaint handling in general, and it supports the dispute choice only under the first reading.")


def _fcr_section(ev: dict) -> str:
    body = [[r["reason_category"], n(r["interactions"]), pct(r["resolved_first_contact"], r["fcr_denominator"]),
             n(round(r["duration_mean_seconds"])) + " s" if r["duration_mean_seconds"] else "n/a",
             n(r["with_duration"])] for r in ev["fcr_by_reason_category"]]
    return table(["reason category", "interactions", "first-contact resolution", "mean handling time",
                  "interactions with a duration"], body) + _fcr_caveat(ev)


def _profile_section(ev: dict) -> str:
    d = ev["dispute_profile"]
    status = ", ".join(f"{r['status']} {pct(r['complaints'], d['complaints'])}" for r in d["status_mix"])
    channels = table(["reception channel", "complaints", "share of disputes"],
                     [[r["channel"], n(r["complaints"]), rate(r["share"])] for r in d["channel_mix"]])
    return (f"Window: {d['first_date']} to {d['last_date']} ({n(d['window_days'])} days). "
            f"{n(d['complaints'])} complaints from {n(d['distinct_customers'])} customers.\n\n"
            f"Current status: {status}.\n\n"
            f"Repeat contact, measured from the complaint history: {pct(d['next_complaint_30d'], d['complaints'])} "
            "are followed by another complaint from the same customer within 30 days. The delivered "
            f"`is_repeat_complainer` flag marks {pct(d['repeat_flag_delivered'], d['complaints'])}, but only "
            f"{n(d['repeat_flag_confirmed'])} of those have an earlier complaint within 90 days in the data, while "
            f"{n(d['prior_complaint_90d'])} complaints do have one. The flag is not used as evidence.\n\n"
            f"Claimed amount present in {pct(d['with_claimed_amount'], d['complaints'])}.\n\n{channels}\n\n"
            + dec.reach_paragraph(ev))


def _demand_section(ev: dict) -> str:
    dem = ev["demand"]
    daily = dem["disputes_daily"][0] if dem["disputes_daily"] else None
    inter = dem["interactions_daily"][0] if dem["interactions_daily"] else None
    rows = []
    for label, s in (("unrecognized-charge complaints", daily), ("all contact-center interactions", inter)):
        if s:
            rows.append([label, n(s["days"]), n(s["mean_per_day"]), n(s["p50_per_day"]), n(s["p95_per_day"]),
                         n(s["max_per_day"])])
    for s in dem["disputes_daily_by_country"]:
        rows.append([f"complaints, {s['country']}", n(s["days"]), n(s["mean_per_day"]), n(s["p50_per_day"]),
                     n(s["p95_per_day"]), n(s["max_per_day"])])
    wd = ", ".join(f"{WEEKDAYS[r['bucket']]} {rate(r['share'])}" for r in dem["disputes_by_weekday"])
    iw = ", ".join(f"{WEEKDAYS[r['bucket']]} {rate(r['share'])}" for r in dem["interactions_by_weekday"])
    hours = dem["disputes_by_hour"]
    lo, hi = (min(hours, key=lambda r: r["contacts"]), max(hours, key=lambda r: r["contacts"])) if hours else (None, None)
    peak = dem["busiest_dispute_weekday_hour"]
    text = [table(["series", "days", "contacts per day, mean", "p50", "p95", "max"], rows), "",
            f"Share of each series by weekday, unrecognized-charge complaints: {wd}. All interactions: {iw}."]
    if lo and hi:
        spread = hi["contacts"] / lo["contacts"] if lo["contacts"] else None
        shape = (f"max/min {spread:.2f}" + (", a nearly flat profile" if spread < 1.25 else "")) if spread else "n/a"
        text.append(f"\nBy hour of day the complaints range from {n(lo['contacts'])} (hour {lo['bucket']}) to "
                    f"{n(hi['contacts'])} (hour {hi['bucket']}) complaints over the whole window ({shape}). The "
                    f"busiest weekday-hour cell ({WEEKDAYS[peak['iso_dow']]} {peak['hour_of_day']}:00) holds "
                    f"{n(peak['contacts'])} complaints in {n(dem['weeks_in_window'])} weeks, "
                    f"{peak['mean_per_week']:.2f} per week.")
    return "\n".join(text)


def _breakdown_section(ev: dict) -> str:
    out = []
    days = ev["dispute_profile"]["window_days"]
    for dim in ("country", "segment"):
        body = [[r["value"], n(r["complaints"]), n(r["customers"]), n(r["disputes_per_1000_customers"]),
                 n(r["disputes_per_1000_customers"] / days * 365) if r["disputes_per_1000_customers"] else "n/a",
                 pct(r["sla_breached"], r["complaints"]), pct(r["escalated"], r["complaints"]),
                 f"{n(r['resolution_days_p50'])} / {n(r['resolution_days_p90'])} (n = {n(r['with_resolution_days'])})",
                 pct(r["complaint_fcr_resolved"], r["complaint_fcr_denominator"])] for r in ev["breakdowns"][dim]]
        out.append(table([dim, "complaints", "customers", f"per 1,000 customers over {n(days)} days",
                          "per 1,000 customers per year", "SLA breached", "escalated", "resolution days p50 / p90",
                          "FCR of Complaint contacts"], body))
    return "\n\n".join(out)


def _limits_section(ev: dict) -> str:
    lim = ev["data_limits"]
    ct, hr, rf = lim["complaint_type_volume"], lim["interaction_hour_of_day"], lim["repeat_flag"]
    desc, tr, links, tx, br = (lim["dispute_descriptions"], lim["transcripts"], lim["complaint_links"],
                               lim["transactions"], lim["registration_branch"])
    sla = {r["sla_breached"]: r for r in lim["sla_vs_resolution_days"]}
    items = []
    if ct:
        items.append(f"Complaint types with a subcategory are near uniform: {ct['n_buckets']} types between "
                     f"{n(ct['min'])} and {n(ct['max'])} complaints (max/min {ct['max_over_min']}, coefficient of "
                     f"variation {ct['coefficient_of_variation']}).")
    if lim["sla_breach_rate_range"]:
        items.append(f"SLA breach rate per complaint type stays between {rate(lim['sla_breach_rate_range'][0])} and "
                     f"{rate(lim['sla_breach_rate_range'][1])}.")
    if True in sla and False in sla:
        items.append(f"`sla_breached` does not follow resolution time: median {n(sla[True]['resolution_days_p50'])} "
                     f"days when breached (n = {n(sla[True]['with_resolution_days'])}) and "
                     f"{n(sla[False]['resolution_days_p50'])} days when not (n = {n(sla[False]['with_resolution_days'])}).")
    if hr:
        items.append(f"Interactions by hour of day: max/min {hr['max_over_min']} across 24 hours "
                     f"(coefficient of variation {hr['coefficient_of_variation']}). Only the weekday pattern varies.")
    items.append(f"The repeat-complainer flag agrees with the history in {n(rf['flagged_and_confirmed'])} of "
                 f"{n(rf['flagged'])} flagged complaints; {n(rf['with_prior_complaint_90d'])} of "
                 f"{n(rf['complaints'])} complaints have an earlier one within 90 days.")
    items.append(f"Unrecognized-charge complaints carry {n(desc['distinct_descriptions'])} distinct description "
                 f"text(s) over {n(desc['complaints'])} rows.")
    items.append(f"Call transcripts: {n(tr['distinct_full_text'])} distinct texts over {n(tr['transcripts'])} rows, "
                 f"{n(tr['distinct_customer_text'])} distinct customer turns, {n(tr['distinct_intents'])} distinct "
                 "detected intent value(s).")
    items.append(f"Complaints linked to an interaction: {pct(links['with_origin_interaction'], links['complaints'])}. "
                 "No complaint names a transaction.")
    items.append(f"Fraud label on transactions: {pct(tx['fraud_labeled'], tx['transactions'], 3)}. Mexican customers' "
                 f"transactions in USD: {pct(tx['mexico_usd'], tx['mexico_transactions'])}.")
    items.append(f"Customers whose registration branch is missing from branches: "
                 f"{pct(br['orphan_branch'], br['customers'], 3)}.")
    cur = lim["dispute_currency_vs_country"]
    if cur:
        items.append("Claimed-amount currency does not follow the customer's country (complaints per pair): "
                     + ", ".join(f"{r['customer_country']} {r['currency']} {n(r['complaints'])}" for r in cur) + ".")
    return "\n".join(f"* {i}" for i in items)


def _conclusion(ev: dict) -> str:
    """What the evidence supports, stated only where the figures back it."""
    rk = ev["workflow_ranking"]
    chosen = next((r for r in rk["rows"] if r["verifiable"] == "yes"), None)
    ranked = (f"By workload the dispute workflow ranks {chosen['rank']} of {len(rk['rows'])} candidates (section 1), "
              "and by complaint volume it ties the next type (section 2). " if chosen else "")
    return ("What this means for the conclusions. Where the outcome metrics (SLA breach, escalation, resolution "
            "time, repeat contact, satisfaction) sit within a few tenths of a point across complaint types, "
            "countries and segments, they cannot rank workflows. " + ranked +
            "The choice of unrecognized charges rests on verifiability: the facts of a "
            "dispute can be checked against records the bank holds (the customer's own transactions, with merchant, "
            "channel, amount and date), which is what the agent's tools verify before any write, and what lets the "
            "evaluation score every automated outcome. The text fields are templated, so language understanding is "
            "evaluated on team-generated scenarios (`ml/reports/results.md`, `eval/report.md`), and the savings "
            "above are projections that assume the offline rates carry over to real traffic.")


def render_report(ev: dict) -> str:
    p = ev["provenance"]
    label = "SYNTHETIC TEST FIXTURE, not organizer data. " if p["source_is_fixture"] else ""
    ml, e = ev["ml"], ev["eval"]
    parts = [
        "# Why unrecognized-charge disputes",
        f"{label}Generated by `data_analytics/run.py` from `{p['warehouse']}` (silver runs "
        f"{', '.join(p['silver_runs'])}; gold run {p['latest_gold_run']}), `{e['source']}` (configuration "
        f"`{e['config']}`, run {e['run_id']}) and `{ml['source']}` (data version {ml['data_version']}). Every figure "
        "below is a query result or a value read from those files, with its denominator and unit. The organizer "
        "dataset is synthetic, so none of this describes a real bank.",
        "## Headline figures", _headline(ev),
        "## 1. Why this workflow: candidate workflows ranked by agent-hours", dec.ranking_section(ev),
        "## 2. Complaint types compared", _workflow_section(ev),
        "## 3. First-contact resolution by contact reason",
        "Contact-center interactions (`was_resolved`, the dictionary's first-call resolution flag). Complaints "
        "cannot be linked to interactions (`origin_interaction_id` is always null), so the dispute workflow's own "
        "FCR is not measurable; the Complaint reason is the closest contact-center proxy.", _fcr_section(ev),
        "## 4. The unrecognized-charge workflow in detail", _profile_section(ev),
        "## 5. Demand patterns and the handoff queue", _demand_section(ev),
        "Hours are as stored; the dictionary gives no timezone, and the pipeline README records that timestamps "
        "look shifted by about six hours from the partition day. Weekday shares may carry the same shift.",
        dec.handoff_section(ev),
        "## 6. By country and segment", _breakdown_section(ev),
        "## 7. Policy thresholds: what they send to a person", dec.threshold_section(ev),
        "## 8. Customer satisfaction", dec.satisfaction_section(ev),
        "## 9. Cost per resolution and the savings range", dec.cost_section(ev),
        "## 10. Where the data is uniform or templated, and what that means", _limits_section(ev),
        _conclusion(ev),
        "## Reproduce",
        f"```bash\nmake gold TARGET=data/{p['warehouse']}\nmake analytics REPORT_WAREHOUSE=data/{p['warehouse']}\n```",
        "The warehouse comes from the bronze/silver pipeline over the organizer data (`make pipeline-s3`, see "
        "`data_engineering/README.md`). A fixture warehouse is refused for the committed reports directory. The "
        "queries live in `data_analytics/` (`figures.py`, `workload.py`, `thresholds.py`, `satisfaction.py`, "
        "`data_limits.py`); the cost model reads `eval/results.json` and `ml/reports/results.json` as committed. "
        "`insights.json` next to this report carries the decision figures for the /insights page.",
    ]
    return "\n\n".join(parts) + "\n"
