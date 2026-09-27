"""Report sections that turn the evidence into decisions: which workflow, what it costs, how big the handoff queue
is, what the policy thresholds route to a person, and what satisfaction data can say. Every number comes from
`ev` (see run.collect)."""

from __future__ import annotations

from data_analytics.cost_model import CENTRAL_HOURLY_COST_USD, ILLUSTRATIVE_HOURLY_COST_USD
from data_analytics.fmt import n, pct, rate, table, usd, usd_llm, usd_year

WEEKDAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _and(items: list[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _short(reach: str) -> str:
    """Reach label without its parenthesis, for tables and sentences."""
    return reach.split(" (")[0] + (" (assumption)" if "assumption" in reach else "")


def _amount_by_type(a: dict) -> str:
    """Where the amount threshold lands by transaction type: the one-month p90 rationale covers only some types."""
    near = [r for r in a["by_type"] if r["share_at_or_above"] is not None and r["share_at_or_above"] <= 0.2]
    far = [r for r in a["by_type"] if r["share_at_or_above"] is not None and r["share_at_or_above"] > 0.5]
    if not far:
        return ""
    return ("The p90 rationale holds for " + _and([r["transaction_type"].lower() + "s" for r in near])
            + f" ({_and([rate(r['share_at_or_above']) for r in near])} at or above the threshold). "
            + _and([r["transaction_type"] for r in far]) + " amounts are larger: "
            + _and([rate(r["share_at_or_above"]) for r in far])
            + " of them reach the threshold, and they carry most of the share the rule sends to a person. How much "
            "of this applies to disputed charges depends on which types customers dispute, which the data does not "
            "record (no complaint names a transaction); the evaluation's case mix is a team choice "
            "(`ml/DATASHEET.md`).")


def ranking_section(ev: dict) -> str:
    rk = ev["workflow_ranking"]
    rows = rk["rows"]
    body = [[r["rank"], r["workflow"], r["proxy"], n(r["contacts_per_year"]), n(r["handling_seconds"]),
             rate(r["first_contact_resolution"]), n(r["agent_hours_per_year"]),
             n(r["unresolved_contact_hours_per_year"]), r["verifiable"], r["record"]] for r in rows]
    text = [
        "The problem statement names four example workflows. The contact center records six coarse contact reasons, "
        "so each workflow is mapped to its closest reason (team judgment), and the dispute workflow is sized from "
        "its complaints with the Complaint reason's handling time and first-contact resolution as proxies.",
        f"* `agent_hours_per_year` = {rk['formula']['agent_hours_per_year']}\n"
        f"* `unresolved_contact_hours_per_year` = {rk['formula']['unresolved_contact_hours_per_year']}",
        table(["rank", "workflow", "data proxy", "contacts per year (complaints for disputes)", "handling time (s)",
               "first-contact resolution", "agent-hours per year", "agent-hours per year on contacts not resolved "
               "first time", "outcome verifiable against records (team judgment)", "record that settles it"], body),
    ]
    chosen = next((r for r in rows if r["verifiable"] == "yes"), None)
    if chosen:
        top = rows[0]
        cc = rk["contact_center"]
        cov = chosen.get("record_coverage")
        times = top["unresolved_contact_hours_per_year"] / chosen["unresolved_contact_hours_per_year"] \
            if chosen["unresolved_contact_hours_per_year"] else None
        text.append(
            f"All contact-center handling adds up to {n(cc['agent_hours_per_year'])} agent-hours per year "
            f"({n(cc['interactions'])} interactions over {n(cc['window_days'])} days). By this measure the dispute "
            f"workflow is small: it ranks {chosen['rank']} of {len(rows)}, with "
            f"{n(chosen['unresolved_contact_hours_per_year'])} agent-hours per year on contacts not resolved first "
            f"time, against {n(top['unresolved_contact_hours_per_year'])} for {top['proxy']}"
            + (f" ({times:.1f} times as many)" if times else "") + ". Other workflows are larger by agent-hours.")
        text.append(
            "Disputes were chosen for verifiability and for the safety of automating them. A dispute is the one "
            "candidate whose right outcome a record the bank holds settles: the charge is in the customer's own "
            "transactions, and the claim window, amount and fraud signals decide what may happen to it"
            + (f"; {pct(cov['with_charge_in_window'], cov['complaints'])} of dispute complaints have at least one "
               f"non-deposit transaction of the customer in the {cov['window_days']} days before the complaint, "
               "where the agent looks for it" if cov else "")
            + ". Every automated outcome can therefore be checked against records, in the evaluation and in "
            "production. Opening a dispute is also a write that touches money, so the cost of an unchecked mistake "
            "is higher than for an informational answer; the team judged that automation should start where each "
            "action can be verified before it is taken.")
        biggest = max(rows, key=lambda r: r["agent_hours_per_year"])
        partly = [r["workflow"].lower() for r in rows if r["verifiable"] == "partly"]
        text.append(
            f"By total agent-hours the largest candidate is {biggest['proxy']} "
            f"({n(biggest['agent_hours_per_year'])} agent-hours per year)."
            + (f" The partly verifiable workflows ({_and(partly)}) would become candidates once the bank records "
               "what the customer asked and what was done, which this data does not." if partly else ""))
    return "\n\n".join(text)


def reach_paragraph(ev: dict) -> str:
    r = ev["channel_reach"]
    dig = {x["channel"]: x for x in r["digital_event_30d_before_complaint"]}
    per = ", ".join(f"{c} {rate(x['share'])}" for c, x in dig.items())
    return (f"A chat flow reaches the disputes filed in App or Web: {pct(r['chat_eligible'], r['complaints'])}. The "
            f"call center receives {pct(r['call_center'], r['complaints'])}. Digital activity does not separate the "
            f"channels: {pct(r['digital_event_30d_total'], r['complaints'])} of dispute complaints come from a "
            f"customer with an app or web event (`digital_events`) in the 30 days before filing ({per}). The data "
            "cannot say which phone disputants would use a chat, so routing them to the flow is kept as a separate, "
            "labeled assumption in section 9.")


def handoff_section(ev: dict) -> str:
    hq = ev["handoff_queue"]
    peak, ipeak = hq["peak_cell"], hq["interactions_peak_cell"]
    body = [[_short(s["reach"]), s["rate"], rate(s["handoff_share_of_disputes"]), n(s["handoffs_per_day_mean"]),
             n(s["handoffs_per_day_p95"]), n(s["handoffs_per_day_max"]), f"{s['handoffs_peak_hour']:.2f}",
             f"{s['handoffs_median_hour']:.2f}", n(s["agent_minutes_peak_hour"]), n(s["agent_minutes_p95_day"])]
            for s in hq["scenarios"]]
    text = [
        "Sizing the human handoff queue. A dispute the bot does not resolve safely goes to a person with the full "
        "handling time (central scenario, section 9). Handoffs = disputes x reach share x (1 - safe automated "
        "resolution). Per-hour figures use the weekday-hour cells of `gold.demand_by_hour`: the busiest cell "
        f"({WEEKDAYS[peak['iso_dow']]} {peak['hour_of_day']}:00) averages {hq['disputes_per_week_peak_cell']:.2f} "
        f"disputes in that hour each week, the median cell {hq['disputes_per_week_median_cell']:.2f}, over "
        f"{n(hq['weeks_in_window'])} weeks.",
        table(["reach", "automation rate", "handoffs, share of disputes", "handoffs per day, mean",
               "per day, p95", "per day, max", "handoffs in the busiest hour", "handoffs in the median hour",
               "agent-minutes in the busiest hour", "agent-minutes on a p95 day"], body)]
    if hq["scenarios"]:
        worst = max(hq["scenarios"], key=lambda s: s["agent_minutes_peak_hour"])
        cc = hq["contact_center_agent_minutes_peak_hour"]
        text.append(
            f"Staffing implication. The heaviest scenario, {_short(worst['reach'])} at the {worst['rate']} rate, needs "
            f"{n(worst['agent_minutes_peak_hour'])} agent-minutes in the busiest hour and "
            f"{n(worst['agent_minutes_p95_day'])} agent-minutes on a p95 day. The contact center already handles "
            f"about {n(cc)} agent-minutes in its own busiest hour ({WEEKDAYS[ipeak['iso_dow']]} "
            f"{ipeak['hour_of_day']}:00, {n(hq['interactions_per_week_peak_cell'])} interactions a week at the "
            "all-reason mean handling time)."
            + (" The dispute handoffs, a fraction of one agent in any hour, do not justify a dedicated team: they fit "
               "in the existing complaint queue, and what needs a target is how fast a person picks up a customer "
               "waiting in the chat, which the data does not measure." if worst["agent_minutes_peak_hour"] < 60
               else "")
            + (" The hourly profile is nearly flat, so a round-the-clock chat needs the same pickup cover at night "
               "as by day (hours as stored, timezone undocumented)." if _flat(ev["demand"]["disputes_by_hour"])
               else ""))
    return "\n\n".join(text)


def _flat(hours: list[dict], limit: float = 1.25) -> bool:
    counts = [r["contacts"] for r in hours]
    return bool(counts) and min(counts) > 0 and max(counts) / min(counts) < limit


def threshold_section(ev: dict) -> str:
    th = ev["thresholds"]
    a, f, e = th["amount"], th["fraud"], th["either"]
    o = a["overall"]
    qs = ", ".join(f"{k} {usd(o[k])}" for k in o if k.startswith("p") and k[1:].isdigit())
    types = table(["transaction type", "disputable charges", f"at or above {usd(a['threshold_usd'])}",
                   "p90 USD amount"],
                  [[r["transaction_type"], n(r["charges"]), pct(r["at_or_above"], r["charges"]), usd(r["p90"])]
                   for r in a["by_type"]])
    labels = table(["is_fraud", "transactions", "with a score", "max score", "median score",
                    f"score at or above {f['score_at_least']:g}"],
                   [[r["is_fraud"], n(r["transactions"]), n(r["with_score"]), n(r["max_score"]), n(r["p50_score"]),
                     n(r["at_or_above"])] for r in f["by_label"]])
    eda = a["eda_month"]
    feda = {r["is_fraud"]: r for r in f["eda_month"]["by_label"]}
    m = a["purchase_p90_by_month"]
    d = f["disputable"]
    fl = feda.get(True)
    text = [
        f"Both thresholds are read from `{th['policy_file']}` (version {th['policy_version']}), and amounts are "
        "converted to USD the way the policy engine does (amount_usd, else the fixed rates of SYN-FX-001). Base: "
        f"{th['base']} (statuses {', '.join(th['disputable_statuses'])}; excluded types "
        f"{', '.join(th['non_charge_types'])}).",
        f"**{a['rule_id']}, USD amount at or above {usd(a['threshold_usd'])}.** {n(o['charges'])} disputable "
        f"charges over the window; USD amount {qs}. The rule sends {pct(o['at_or_above'], o['charges'])} of them to "
        f"a person.", types, _amount_by_type(a),
        f"Reproducing the one-month analysis the rules.yaml comment quotes: in {eda['month']} the warehouse holds "
        f"{n(eda['transactions'])} transactions, {n(eda['purchases'])} of them purchases, "
        f"{n(eda['purchases_with_usd'])} with amount_usd, whose p90 is {usd(eda['purchase_p90_usd'])}. Over the "
        f"{m['months']} months of the window the monthly purchase p90 ranges from {usd(m['min'])} to "
        f"{usd(m['max'])}, so the month chosen was typical and the threshold sits near the purchase p90 of any "
        "month.",
        f"**{f['rule_id']}, fraud score at or above {f['score_at_least']:g}, or the fraud flag.**", labels,
        _fraud_reading(f, d, eda["month"], feda, fl),
        f"Either threshold together sends {pct(e['routed'], e['charges'])} of disputable charges to a person "
        f"({n(e['without_usd'])} charges lack a USD amount after conversion). The claim-window rule depends on the "
        "day of the conversation and is not counted here.",
    ]
    return "\n\n".join(text)


def _fraud_reading(f: dict, d: dict, month: str, feda: dict, fl: dict | None) -> str:
    t = f"{f['score_at_least']:g}"
    text = (f"The largest score of an unflagged transaction is {n(f['unflagged_max_score'])}; "
            f"{n(f['flagged_above_unflagged_max'])} of {n(f['flagged_with_score'])} flagged transactions with a "
            f"score sit above it. Among disputable charges the rule routes {pct(d['routed'], d['charges'], 3)} to a "
            f"person, {n(d['routed_by_score_only'])} of them by the score without the flag.")
    if d["routed_by_score_only"] == 0:
        text += (" In this data the fraud rule sends exactly the flagged charges, and any score threshold above the "
                 f"unflagged maximum would route the same charges. The value {t} keeps a margin above that maximum "
                 "for data where score and flag disagree; it is a team choice with no outcome data behind it, and it "
                 "does not change the workload here.")
    if fl and False in feda:
        text += (f" In {month}: {n(fl['transactions'])} flagged transactions, {n(fl['at_or_above'])} of them at or "
                 f"above {t}; unflagged maximum {n(feda[False]['max_score'])}. The counts in the rules.yaml comment "
                 "come from the warehouse as it was when the comment was written.")
    return text


def satisfaction_section(ev: dict) -> str:
    sat = ev["satisfaction"]
    s, c = sat["surveys"], sat["complaints"]
    res = {r["was_resolved"]: r for r in s["by_first_contact_resolution"]}
    gaps = [r["mean_resolved"] - r["mean_unresolved"] for r in s["by_reason"]
            if r["mean_resolved"] is not None and r["mean_unresolved"] is not None]
    reasons = ", ".join(f"{r['reason_category']} {r['mean_score']:.2f} (FCR {rate(r['fcr_of_surveyed'])})"
                        for r in s["by_reason"])
    disp = next((r for r in c["by_is_dispute"] if r["is_unrecognized_charge"]), None)
    other = next((r for r in c["by_is_dispute"] if r["is_unrecognized_charge"] is False), None)
    dist = ", ".join(f"{r['score']}: {n(r['complaints'])}" for r in c["dispute_score_distribution"])
    statuses = _and([r["status"] for r in c["statuses_with_score"]])
    scored = sum(r["with_score"] for r in c["statuses_with_score"])
    days = ", ".join(f"{r['resolution_days']} days {r['mean_score']:.2f} (n = {n(r['with_score'])})"
                     for r in c["all_complaints_by_resolution_days"])
    text = [
        "Surveys link to contact-center interactions, never to complaints: "
        f"{pct(s['links']['linked_to_interaction'], s['links']['surveys'])} "
        "of surveys carry an interaction id found in the contact center, and complaints carry no interaction id "
        "(section 10), so no survey can be tied to a dispute."]
    if True in res and False in res:
        text.append(
            f"CSAT surveys ({n(s['scale']['surveys'])}, scores {s['scale']['min_score']} to {s['scale']['max_score']} "
            f"as observed) average {res[True]['mean_score']:.2f} when the contact was resolved first time (n = "
            f"{n(res[True]['surveys'])}) and {res[False]['mean_score']:.2f} when it was not (n = "
            f"{n(res[False]['surveys'])}). The gap is between {min(gaps):.2f} and {max(gaps):.2f} points in every "
            f"contact reason, and mean CSAT by reason follows its first-contact resolution: {reasons}. In this data "
            "satisfaction moves with first-contact resolution, which makes it the customer outcome to target: a "
            "dispute taken end to end in one conversation is what the surveys reward. The dispute workflow's own "
            "effect on CSAT cannot be measured, since disputes cannot be linked to a contact.")
    if disp and other:
        text.append(
            "The complaint-level score (`resolution_satisfaction`) exists for "
            f"{pct(disp['with_score'], disp['complaints'])} "
            f"of disputes; across all types it is present only on complaints with status {statuses} ({n(scored)} "
            f"complaints). Its values ({disp['min_score']} to "
            f"{disp['max_score']}) are spread almost evenly ({dist}); the mean is {disp['mean_score']:.2f} for "
            f"disputes and {other['mean_score']:.2f} for other complaints, and across all complaints it does not "
            f"follow resolution time: {days}. It cannot rank workflows or measure a dispute outcome.")
    return "\n\n".join(text)


def cost_section(ev: dict) -> str:
    cm = ev["cost_model"]
    inputs = table(["input", "value", "unit", "kind", "source", "note"],
                   [[k, "not defined" if v["value"] is None else v["value"], v["unit"], v["kind"], v["source"],
                     v["note"]] for k, v in cm["inputs"].items()])
    formula = "\n".join(f"* `{k}` = {v}" for k, v in cm["formula"].items())
    cpr = cm["cost_per_resolution"]
    cpr_body = [[usd(r), usd(cpr["human_usd_per_dispute_by_hourly_cost"][str(r)]),
                 usd_llm(cpr["llm_usd_per_safe_automated_resolution"]), usd_llm(cpr["llm_usd_per_conversation"]),
                 f"USD {cpr['cost_per_routed_dispute_with_bot_by_hourly_cost'][str(r)]:.4f}"]
                for r in ILLUSTRATIVE_HOURLY_COST_USD] if cpr["human_usd_per_dispute_by_hourly_cost"] else []
    cpr_table = table(["USD per agent-hour (placeholder)", "human cost per dispute intake",
                       "LLM cost per safe automated resolution", "LLM cost per conversation",
                       "cost per dispute sent to the bot, conservative rate (failed attempts at full human cost)"],
                      cpr_body)
    central = [p for p in cm["projections"] if p["scenario"].startswith("central")]
    proj = []
    for p in central:
        m = next(x for x in p["money"] if x["hourly_cost_usd"] == CENTRAL_HOURLY_COST_USD)
        proj.append([p["reach"], f"{p['rate']} ({rate(p['safe_automated_resolution_rate'])})",
                     n(p["disputes_routed_per_year"]), n(p["hours_saved_per_year"]), n(p["human_hours_left_per_year"]),
                     rate(p["share_of_contact_center_hours"], 2), usd(p["llm_usd_per_year"]),
                     usd_year(m["net_savings_per_year_usd"])])
    proj_table = table(["reach", "automation rate", "disputes sent to the bot per year", "agent-hours saved per year",
                        "agent-hours still needed per year (handoffs)", "share of all contact-center agent-hours",
                        "LLM USD per year", f"net USD saved per year at USD {CENTRAL_HOURLY_COST_USD:g} per hour"],
                       proj)
    sens = []
    for p in cm["projections"]:
        money = {m["hourly_cost_usd"]: m for m in p["money"]}
        sens.append([p["scenario"].split(":")[0], _short(p["reach"]), p["rate"],
                     n(p["human_minutes_per_dispute"]), n(p["hours_saved_per_year"])]
                    + [usd_year(money[r]["net_savings_per_year_usd"]) for r in ILLUSTRATIVE_HOURLY_COST_USD]
                    + [usd(money[CENTRAL_HOURLY_COST_USD]["break_even_bot_usd_per_conversation"])])
    sens_table = table(["handling", "reach", "rate", "human minutes per dispute", "agent-hours saved per year"]
                       + [f"net USD per year at USD {r:g}/h" for r in ILLUSTRATIVE_HOURLY_COST_USD]
                       + [f"break-even bot USD per conversation at USD {CENTRAL_HOURLY_COST_USD:g}/h"], sens)
    lo = min(central, key=lambda p: p["hours_saved_per_year"]) if central else None
    hi = max(central, key=lambda p: p["hours_saved_per_year"]) if central else None
    ev_ = ev["eval"]
    text = [f"Scope: {cm['scope']}.", formula, inputs, "### Cost per resolution", cpr_table,
            f"The LLM cost is measured: {usd_llm(ev_['llm_cost_usd_total'])} of tokens "
            f"over {n(ev_['conversations'])} conversations of configuration `{ev_['config']}` (run {ev_['run_id']}, "
            f"`{ev_['source']}`), priced at list price. Hosting and compute are not measured; the break-even column "
            "below is the most a bot conversation could cost in total and still save money.",
            "### Savings range (central handling time)", proj_table]
    if lo and hi:
        text.append(
            f"The range runs from {n(lo['hours_saved_per_year'])} agent-hours per year ({lo['rate']} rate, "
            f"{lo['reach'].split(' (')[0]}) to {n(hi['hours_saved_per_year'])} ({hi['rate']} rate, "
            f"{hi['reach'].split(' (')[0]}), {rate(lo['share_of_contact_center_hours'], 2)} to "
            f"{rate(hi['share_of_contact_center_hours'], 2)} of all contact-center agent-hours. The conservative rate "
            "is the deployed configuration's end-to-end result on simulated conversations; the optimistic rate is the "
            "decision component alone on the split used for error analysis. Labor savings at this scale are small; "
            "section 1 gives the reason for the choice.")
    text += ["### Sensitivity: every handling scenario, reach and rate", sens_table,
             "Reading the break-even column: a bot conversation (model calls, hosting, review of automated outcomes) "
             "that costs less than that value saves money under the scenario's assumptions. The measured LLM part is "
             f"{usd_llm(ev_['llm_usd_per_conversation'])} per conversation."]
    return "\n\n".join(text)
