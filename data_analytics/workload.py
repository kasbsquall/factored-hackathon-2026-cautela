"""Workload evidence: candidate workflows ranked by agent-hours, how many dispute customers each channel reaches, and
the size of the human handoff queue the projected automation rate would leave.

The contact center records only six coarse contact reasons, so each workflow the problem statement names is mapped
to the closest reason. That mapping and the "outcome verifiable against records" column are team judgments, stated
as such in every output; the volumes, handling times and first-contact resolution rates are query results.
"""

from __future__ import annotations

import duckdb

from agent.tools.contracts import FindCandidateChargesInput
from agent.tools.impl import NON_CHARGE_TYPES
from data_analytics.figures import one, ratio, rows

# The agent looks for the disputed charge in this many days before the conversation (the service's own default).
CANDIDATE_WINDOW_DAYS = FindCandidateChargesInput.model_fields["window_days"].default

# Team judgment: problem-statement workflow -> closest contact reason, and whether a record proves the right answer.
# "yes": a record the bank holds settles the outcome; "partly": records answer part of it; "no": no record does.
CANDIDATES = (
    {"workflow": "Account or payment inquiries", "proxy": "contact reason Transactional",
     "reason_category": "Transactional", "verifiable": "partly",
     "record": "balances and transactions answer the question, but what the customer asked is not recorded "
               "(one detected-intent value over all transcripts)"},
    {"workflow": "Card-service support", "proxy": "contact reason Product", "reason_category": "Product",
     "verifiable": "partly",
     "record": "product status and expiry are recorded; card actions (block, replace) have no outcome record"},
    {"workflow": "Credit-product information and eligibility", "proxy": "contact reason Commercial",
     "reason_category": "Commercial", "verifiable": "no",
     "record": "no approved eligibility policy in the data; the problem statement requires a labeled synthetic "
               "policy service"},
    {"workflow": "(not a named workflow) general complaints", "proxy": "contact reason Complaint",
     "reason_category": "Complaint", "verifiable": "no",
     "record": "complaint types are mixed and 0 complaints link to a contact; only the dispute subtype names a "
               "fact a record can check"},
    {"workflow": "(not a named workflow) technical support", "proxy": "contact reason Technical",
     "reason_category": "Technical", "verifiable": "no", "record": "app faults have no record in the data"},
    {"workflow": "(not a named workflow) retention", "proxy": "contact reason Retención",
     "reason_category": "Retención", "verifiable": "no",
     "record": "a retention offer has no right answer in the records"},
)
DISPUTE_CANDIDATE = {
    "workflow": "Transaction-dispute intake (unrecognized charge, the chosen workflow)",
    "proxy": "Cargo no reconocido complaints; handling time and FCR of the Complaint reason",
    "verifiable": "yes",
    "record": "the customer's own transactions, with merchant, channel, amount and date",
}


def interaction_window_days(con: duckdb.DuckDBPyConnection) -> int:
    return one(con, "SELECT date_diff('day', min(event_date), max(event_date)) + 1 AS d FROM gold.demand_by_day "
                    "WHERE source_system = 'interactions'")["d"]


def dispute_record_coverage(con: duckdb.DuckDBPyConnection) -> dict:
    """Dispute complaints whose customer has at least one disputable charge in the agent's search window."""
    days = int(CANDIDATE_WINDOW_DAYS)
    out = rows(con, f"""
        WITH d AS (SELECT complaint_id, customer_id, creation_date FROM gold.complaint_facts
                   WHERE is_unrecognized_charge),
        hit AS (SELECT DISTINCT d.complaint_id FROM d JOIN gold.customer_transactions t
                ON t.customer_id = d.customer_id
               AND t.transaction_date >= d.creation_date - INTERVAL {days} DAY
               AND t.transaction_date <= d.creation_date
               AND t.transaction_type NOT IN (SELECT unnest(?)))
        SELECT count(*) AS complaints, count(hit.complaint_id) AS with_charge_in_window, {days} AS window_days
        FROM d LEFT JOIN hit USING (complaint_id)""", [list(NON_CHARGE_TYPES)])[0]
    out["share"] = ratio(out["with_charge_in_window"], out["complaints"])
    return out


def contact_center_hours(categories: list[dict], days: int) -> dict:
    """All contact-center agent-hours per year: interactions x mean handling time, summed over reasons."""
    total = sum(r["interactions"] * (r["duration_mean_seconds"] or 0) for r in categories) / 3600
    return {"interactions": sum(r["interactions"] for r in categories), "window_days": days,
            "agent_hours_in_window": round(total, 1), "agent_hours_per_year": round(total / days * 365, 1)}


def _row(meta: dict, per_year: float, seconds: float, fcr: float, basis: str) -> dict:
    hours = per_year * seconds / 3600
    return {**meta, "contacts_per_year": round(per_year, 1), "handling_seconds": round(seconds, 1),
            "first_contact_resolution": round(fcr, 4), "agent_hours_per_year": round(hours, 1),
            "unresolved_contact_hours_per_year": round(hours * (1 - fcr), 1), "basis": basis}


def workflow_ranking(con: duckdb.DuckDBPyConnection, categories: list[dict], profile: dict) -> dict:
    """Candidate workflows ranked by agent-hours per year spent on contacts not resolved at first contact."""
    days = interaction_window_days(con)
    by_reason = {r["reason_category"]: r for r in categories}
    out = []
    for meta in CANDIDATES:
        r = by_reason.get(meta["reason_category"])
        if not r or not r["duration_mean_seconds"]:
            continue
        out.append(_row(meta, r["interactions"] / days * 365, r["duration_mean_seconds"], r["fcr_rate"],
                        f"{r['interactions']:,} interactions in {days:,} days; FCR {r['resolved_first_contact']:,} / "
                        f"{r['fcr_denominator']:,}; handling time mean of {r['with_duration']:,}"))
    complaint = by_reason.get("Complaint")
    if complaint and complaint["duration_mean_seconds"]:
        cov = dispute_record_coverage(con)
        meta = {**DISPUTE_CANDIDATE, "record_coverage": cov}
        out.append(_row(meta, profile["complaints"] / profile["window_days"] * 365,
                        complaint["duration_mean_seconds"], complaint["fcr_rate"],
                        f"{profile['complaints']:,} complaints in {profile['window_days']:,} days; handling time "
                        "and FCR are Complaint-reason proxies (complaints cannot be linked to contacts)"))
    out.sort(key=lambda r: -r["unresolved_contact_hours_per_year"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return {"formula": {"agent_hours_per_year": "contacts_per_year * handling_seconds / 3600",
                        "unresolved_contact_hours_per_year": "agent_hours_per_year * (1 - first_contact_resolution)"},
            "judgment_columns": ["workflow", "proxy", "verifiable", "record"],
            "contact_center": contact_center_hours(categories, days), "rows": out}


def channel_reach(con: duckdb.DuckDBPyConnection, profile: dict) -> dict:
    """Dispute reception channels a chat flow reaches, and digital activity of the complainants before filing."""
    mix = {r["channel"]: r["complaints"] for r in profile["channel_mix"]}
    n = profile["complaints"]
    chat = mix.get("App", 0) + mix.get("Web", 0)
    call = mix.get("Call Center", 0)
    digital = rows(con, """
        WITH d AS (SELECT complaint_id, customer_id, creation_date, reception_channel FROM gold.complaint_facts
                   WHERE is_unrecognized_charge),
        hit AS (SELECT DISTINCT d.complaint_id FROM d JOIN silver.digital_events e ON e.customer_id = d.customer_id
                AND e.event_date >= d.creation_date - INTERVAL 30 DAY AND e.event_date < d.creation_date)
        SELECT d.reception_channel AS channel, count(*) AS complaints,
               count(hit.complaint_id) AS with_digital_event_30d
        FROM d LEFT JOIN hit USING (complaint_id) GROUP BY 1 ORDER BY 2 DESC, 1""")
    for r in digital:
        r["share"] = ratio(r["with_digital_event_30d"], r["complaints"])
    return {"complaints": n, "chat_eligible": chat, "chat_eligible_share": ratio(chat, n),
            "chat_eligible_channels": ["App", "Web"], "call_center": call, "call_center_share": ratio(call, n),
            "chat_plus_call_center_share": ratio(chat + call, n),
            "digital_event_30d_before_complaint": digital,
            "digital_event_30d_total": sum(r["with_digital_event_30d"] for r in digital)}


def handoff_queue(con: duckdb.DuckDBPyConnection, demand: dict, projections: list[dict],
                  all_reasons_seconds: float | None) -> dict:
    """Human handoffs the projection leaves, per day and per weekday-hour, against the contact center's own load."""
    weeks = demand["weeks_in_window"]
    cells = rows(con, """
        SELECT source_system, iso_dow, hour_of_day, sum(contacts)::BIGINT AS contacts FROM gold.demand_by_hour
        WHERE (source_system = 'complaints' AND is_unrecognized_charge) OR source_system = 'interactions'
        GROUP BY ALL""")
    disputes = sorted(c["contacts"] for c in cells if c["source_system"] == "complaints")
    inter = [c for c in cells if c["source_system"] == "interactions"]
    peak_d = max((c for c in cells if c["source_system"] == "complaints"), key=lambda c: c["contacts"])
    peak_i = max(inter, key=lambda c: c["contacts"])
    median_cell = disputes[len(disputes) // 2] if disputes else None
    daily = demand["disputes_daily"][0]
    base = {"weeks_in_window": weeks, "weekday_hour_cells": len(disputes),
            "disputes_per_week_peak_cell": round(peak_d["contacts"] / weeks, 3),
            "peak_cell": {"iso_dow": peak_d["iso_dow"], "hour_of_day": peak_d["hour_of_day"]},
            "disputes_per_week_median_cell": round(median_cell / weeks, 3) if median_cell else None,
            "disputes_per_day": {k: daily[k] for k in ("mean_per_day", "p95_per_day", "max_per_day")},
            "interactions_per_week_peak_cell": round(peak_i["contacts"] / weeks, 1),
            "interactions_peak_cell": {"iso_dow": peak_i["iso_dow"], "hour_of_day": peak_i["hour_of_day"]},
            "contact_center_agent_minutes_peak_hour": (round(peak_i["contacts"] / weeks * all_reasons_seconds / 60, 1)
                                                        if all_reasons_seconds else None)}
    scen = []
    for p in projections:
        if not p["scenario"].startswith("central"):
            continue
        share = p["reach_share"] * (1 - p["safe_automated_resolution_rate"])
        minutes = p["human_minutes_per_dispute"]
        peak = base["disputes_per_week_peak_cell"] * share
        scen.append({"reach": p["reach"], "rate": p["rate"], "handoff_share_of_disputes": round(share, 4),
                     "handoffs_per_day_mean": round(daily["mean_per_day"] * share, 2),
                     "handoffs_per_day_p95": round(daily["p95_per_day"] * share, 2),
                     "handoffs_per_day_max": round(daily["max_per_day"] * share, 2),
                     "handoffs_peak_hour": round(peak, 3),
                     "handoffs_median_hour": round((base["disputes_per_week_median_cell"] or 0) * share, 3),
                     "agent_minutes_peak_hour": round(peak * minutes, 2),
                     "agent_minutes_p95_day": round(daily["p95_per_day"] * share * minutes, 1),
                     "human_minutes_per_handoff": minutes})
    return {**base, "scenarios": scen}
