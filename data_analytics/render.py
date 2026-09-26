"""Render why-this-workflow.md from the collected evidence. Every number printed here comes from `ev`, which
`run.collect` filled from warehouse queries and ml/reports/results.json; nothing is typed in by hand."""

from __future__ import annotations

WEEKDAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def n(x) -> str:
    return "n/a" if x is None else f"{x:,}" if isinstance(x, int) else f"{x:,.1f}"


def pct(k, d, digits: int = 1) -> str:
    return "n/a" if not d or k is None else f"{100 * k / d:.{digits}f}% ({k:,} / {d:,})"


def rate(v, digits: int = 1) -> str:
    return "n/a" if v is None else f"{100 * v:.{digits}f}%"


def table(headers: list[str], body: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def _headline(ev: dict) -> str:
    d, ml, w = ev["dispute_profile"], ev["ml"], ev["workflows"]
    top = next((r for r in w if r["is_unrecognized_charge"]), None)
    worst = ev["fcr_by_reason_category"][0] if ev["fcr_by_reason_category"] else None
    central = next((s for s in ev["cost_model"]["scenarios"] if s["scenario"].startswith("central")), None)
    lines = [
        f"* Unrecognized-charge complaints (\"Cargo no reconocido\"): {n(d['complaints'])} of "
        f"{n(top['all_complaints']) if top else 'n/a'} complaints, {rate(top and top['share_of_complaints'])}, "
        f"volume rank {top['volume_rank'] if top else 'n/a'} of {len(w)} complaint types.",
        f"* SLA breached: {pct(d['sla_breached'], d['complaints'])}. Currently escalated: "
        f"{pct(d['escalated'], d['complaints'])}.",
        f"* Resolution time: p50 {n(d['resolution_days_p50'])} days, p90 {n(d['resolution_days_p90'])} days, over the "
        f"{n(d['with_resolution_days'])} complaints that have one ({pct(d['with_resolution_days'], d['complaints'])}). "
        "First response: p50 "
        f"{n(d['first_response_hours_p50'])} h, p90 {n(d['first_response_hours_p90'])} h (n = {n(d['with_first_response'])}).",
    ]
    daily = ev["demand"]["disputes_daily"][0] if ev["demand"]["disputes_daily"] else None
    if daily and d["channel_mix"]:
        ch = d["channel_mix"][0]
        lines.append(f"* Demand: {n(daily['mean_per_day'])} unrecognized-charge complaints per day on average, p95 "
                     f"{n(daily['p95_per_day'])}, max {n(daily['max_per_day'])} ({n(daily['days'])} calendar days). "
                     f"Largest channel: {ch['channel']}, {pct(ch['complaints'], d['complaints'])}.")
    if worst:
        lines.append(f"* Lowest first-contact resolution in the contact center: {worst['reason_category']}, "
                     f"{pct(worst['resolved_first_contact'], worst['fcr_denominator'])} of interactions.")
    lines.append(f"* Safe automated resolution of the proposed decision component: "
                 f"{rate(ml['safe_automated_resolution_rate'])} of {n(ml['n_cases'])} held-out generated cases "
                 f"(95% CI {rate(ml['safe_automated_resolution_ci95'][0])} to "
                 f"{rate(ml['safe_automated_resolution_ci95'][1])}), from `{ml['source']}`.")
    if central:
        lines.append(f"* Projection, central scenario: {n(central['hours_saved_per_year'])} agent-hours of intake "
                     f"handling per year avoided out of {n(central['human_hours_per_year'])}. This is an offline "
                     "projection with labeled assumptions, not a measured saving.")
    return "\n".join(lines)


def _workflow_section(ev: dict) -> str:
    w = ev["workflows"]
    body = [[r["volume_rank"], r["complaint_type"], n(r["complaints"]),
             f"{rate(r['share_of_complaints'])} [{rate(r['share_ci95'][0])}, {rate(r['share_ci95'][1])}]",
             pct(r["sla_breached"], r["complaints"]), pct(r["escalated"], r["complaints"]),
             f"{n(r['resolution_days_p50'])} / {n(r['resolution_days_p90'])} (n = {n(r['with_resolution_days'])})",
             pct(r["next_complaint_30d"], r["complaints"])] for r in w]
    text = [table(["rank", "complaint type", "complaints", "share [95% CI]", "SLA breached", "escalated",
                   "resolution days p50 / p90", "another complaint within 30 days"], body)]
    if len(w) >= 2 and w[0]["share_ci95"] and w[1]["share_ci95"]:
        overlap = w[0]["share_ci95"][0] <= w[1]["share_ci95"][1]
        text.append(
            f"\nThe first two types differ by {n(w[0]['complaints'] - w[1]['complaints'])} complaints. "
            + ("Their share intervals overlap, so the volume ranking alone does not separate them."
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
                  "n with duration"], body) + _fcr_caveat(ev)


def _profile_section(ev: dict) -> str:
    d = ev["dispute_profile"]
    status = ", ".join(f"{r['status']} {pct(r['complaints'], d['complaints'])}" for r in d["status_mix"])
    channels = table(["reception channel", "complaints", "share"],
                     [[r["channel"], n(r["complaints"]), rate(r["share"])] for r in d["channel_mix"]])
    return (f"Window: {d['first_date']} to {d['last_date']} ({n(d['window_days'])} days). "
            f"{n(d['complaints'])} complaints from {n(d['distinct_customers'])} customers.\n\n"
            f"Current status: {status}.\n\n"
            f"Repeat contact, measured from the complaint history: {pct(d['next_complaint_30d'], d['complaints'])} "
            "are followed by another complaint from the same customer within 30 days. The delivered "
            f"`is_repeat_complainer` flag marks {pct(d['repeat_flag_delivered'], d['complaints'])}, but only "
            f"{n(d['repeat_flag_confirmed'])} of those have an earlier complaint within 90 days in the data, while "
            f"{n(d['prior_complaint_90d'])} complaints do have one. The flag is not used as evidence.\n\n"
            f"Claimed amount present in {pct(d['with_claimed_amount'], d['complaints'])}.\n\n{channels}")


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
    text = [table(["series", "days", "mean per day", "p50", "p95", "max"], rows), "",
            f"By weekday, unrecognized-charge complaints: {wd}. All interactions: {iw}."]
    if lo and hi:
        spread = hi["contacts"] / lo["contacts"] if lo["contacts"] else None
        shape = (f"max/min {spread:.2f}" + (", a nearly flat profile" if spread < 1.25 else "")) if spread else "n/a"
        text.append(f"\nBy hour of day the complaints range from {n(lo['contacts'])} (hour {lo['bucket']}) to "
                    f"{n(hi['contacts'])} (hour {hi['bucket']}) over the whole window ({shape}). The "
                    f"busiest weekday-hour cell ({WEEKDAYS[peak['iso_dow']]} {peak['hour_of_day']}:00) holds "
                    f"{n(peak['contacts'])} complaints in {n(dem['weeks_in_window'])} weeks, "
                    f"{n(peak['mean_per_week'])} per week.")
    return "\n".join(text)


def _breakdown_section(ev: dict) -> str:
    out = []
    for dim in ("country", "segment"):
        body = [[r["value"], n(r["complaints"]), n(r["customers"]), n(r["disputes_per_1000_customers"]),
                 pct(r["sla_breached"], r["complaints"]), pct(r["escalated"], r["complaints"]),
                 f"{n(r['resolution_days_p50'])} / {n(r['resolution_days_p90'])} (n = {n(r['with_resolution_days'])})",
                 pct(r["complaint_fcr_resolved"], r["complaint_fcr_denominator"])] for r in ev["breakdowns"][dim]]
        out.append(table([dim, "complaints", "customers", "per 1,000 customers", "SLA breached", "escalated",
                          "resolution days p50 / p90", "FCR of Complaint contacts"], body))
    return "\n\n".join(out)


def _cost_section(ev: dict) -> str:
    cm = ev["cost_model"]
    inputs = table(["input", "value", "unit", "kind", "source", "note"],
                   [[k, "not defined" if v["value"] is None else v["value"], v["unit"], v["kind"], v["source"],
                     v["note"]] for k, v in cm["inputs"].items()])
    body = []
    for s in cm["scenarios"]:
        for m in s["money"]:
            body.append([s["scenario"], n(s["human_minutes_per_dispute"]), n(s["human_hours_per_year"]),
                         n(s["hours_saved_per_year"]), m["hourly_cost_usd"], m["human_cost_per_dispute_usd"],
                         n(m["savings_per_year_usd"]), m["break_even_automation_cost_per_attempted_case_usd"]])
    outputs = table(["scenario", "human min per dispute", "human h per year", "h saved per year",
                     "USD per hour (placeholder)", "human USD per dispute", "USD saved per year",
                     "break-even automation USD per attempted case"], body)
    formula = "\n".join(f"* `{k}` = {v}" for k, v in cm["formula"].items())
    return (f"Scope: {cm['scope']}.\n\n{formula}\n\n{inputs}\n\n{outputs}\n\n"
            "Reading the break-even column: an automated attempt that costs less than that value per case (model "
            "calls, infrastructure, review of the automated outcomes) saves money under the scenario's assumptions. "
            "The automation cost itself is not defined yet because the evaluation has not been run with an LLM.")


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
        items.append("Claimed-amount currency does not follow the customer's country: " + ", ".join(
            f"{r['customer_country']} {r['currency']} {n(r['complaints'])}" for r in cur) + ".")
    return "\n".join(f"* {i}" for i in items)


def _conclusion(ev: dict) -> str:
    """What the evidence supports, stated only where the figures back it."""
    w, cats = ev["workflows"], ev["fcr_by_reason_category"]
    top = next((r for r in w if r["is_unrecognized_charge"]), None)
    runner = next((r for r in w if r is not top), None)
    reasons = []
    if top and top["volume_rank"] == 1:
        tied = runner and runner["share_ci95"] and top["share_ci95"][0] <= runner["share_ci95"][1]
        reasons.append("it is the largest complaint type" + (" (tied within sampling error with the next one)"
                                                              if tied else ""))
    elif top:
        reasons.append(f"it ranks {top['volume_rank']} of {len(w)} complaint types by volume")
    if cats and cats[0]["reason_category"] == "Complaint":
        reasons.append("complaint contacts have the lowest first-contact resolution in the contact center, with "
                       "the caveat of section 2 that disputes cannot be tied to a contact reason")
    reasons.append("its facts can be checked against records the bank holds (the customer's own transactions, "
                   "with merchant, channel, amount and date), which is what the agent's tools verify")
    return ("What this means for the conclusions. Where the outcome metrics (SLA breach, escalation, resolution "
            "time, repeat contact) sit within a few tenths of a point across complaint types, countries and "
            "segments, they cannot rank workflows. The choice of unrecognized charges rests on what the data does "
            "support: " + "; ".join(reasons) + ". The text fields are templated, so language understanding is "
            "evaluated on team-generated scenarios (`ml/reports/results.md`), and the savings above are "
            "projections that assume the offline rate carries over to real traffic.")


def render_report(ev: dict) -> str:
    p = ev["provenance"]
    label = "SYNTHETIC TEST FIXTURE, not organizer data. " if p["source_is_fixture"] else ""
    ml = ev["ml"]
    parts = [
        "# Why unrecognized-charge disputes",
        f"{label}Generated by `data_analytics/run.py` from `{p['warehouse']}` (silver runs "
        f"{', '.join(p['silver_runs'])}; gold run {p['latest_gold_run']}) and `{ml['source']}` (data version "
        f"{ml['data_version']}). Every figure below is a query result with its denominator. The organizer dataset "
        "is synthetic, so none of this describes a real bank.",
        "## Headline figures", _headline(ev),
        "## 1. Complaint types compared", _workflow_section(ev),
        "## 2. First-contact resolution by contact reason",
        "Contact-center interactions (`was_resolved`, the dictionary's first-call resolution flag). Complaints "
        "cannot be linked to interactions (`origin_interaction_id` is always null), so the dispute workflow's own "
        "FCR is not measurable; the Complaint reason is the closest contact-center proxy.", _fcr_section(ev),
        "## 3. The unrecognized-charge workflow in detail", _profile_section(ev),
        "## 4. Demand patterns", _demand_section(ev),
        "Hours are as stored; the dictionary gives no timezone, and the pipeline README records that timestamps "
        "look shifted by about six hours from the partition day. Weekday shares may carry the same shift.",
        "## 5. By country and segment", _breakdown_section(ev),
        "## 6. Cost per resolution and what automation would save", _cost_section(ev),
        "## 7. Where the data is uniform or templated, and what that means", _limits_section(ev),
        _conclusion(ev),
        "## Reproduce",
        f"```bash\nmake gold TARGET=data/{p['warehouse']}\nmake analytics REPORT_WAREHOUSE=data/{p['warehouse']}\n```",
        "The warehouse comes from the bronze/silver pipeline over the organizer data (`make pipeline-s3`, see "
        "`data_engineering/README.md`). A fixture warehouse is refused for the committed reports directory.",
    ]
    return "\n\n".join(parts) + "\n"
