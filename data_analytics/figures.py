"""Figures for the workflow evidence, read from the gold layer (and silver only for data-quality diagnostics).

Every figure keeps its numerator and denominator so the report never quotes a rate without them. Functions take a
read-only DuckDB connection and return plain dicts and lists that serialize to JSON.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

DISPUTES = "FROM gold.complaint_facts WHERE is_unrecognized_charge"


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def one(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    return rows(con, sql)[0]


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval for k successes out of n."""
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(centre - half, 4), round(centre + half, 4)]


def ratio(k: int | None, n: int | None) -> float | None:
    return round(k / n, 6) if k is not None and n else None


def workflow_table(con: duckdb.DuckDBPyConnection) -> list[dict]:
    out = rows(con, "SELECT * EXCLUDE (_source_table, _source_key, _source_run_ids, _gold_run_id) "
                    "FROM gold.workflow_selection ORDER BY volume_rank")
    for r in out:
        r["share_ci95"] = wilson(r["complaints"], r["all_complaints"])
        r["sla_breach_ci95"] = wilson(r["sla_breached"], r["complaints"])
    return out


def dispute_profile(con: duckdb.DuckDBPyConnection) -> dict:
    p = one(con, f"""
        SELECT count(*) AS complaints, count(DISTINCT customer_id) AS distinct_customers,
               count(*) FILTER (WHERE sla_breached) AS sla_breached,
               count(*) FILTER (WHERE is_escalated) AS escalated,
               count(resolution_days) AS with_resolution_days,
               quantile_cont(resolution_days, 0.5) AS resolution_days_p50,
               quantile_cont(resolution_days, 0.9) AS resolution_days_p90,
               count(first_response_hours) AS with_first_response,
               quantile_cont(first_response_hours, 0.5) AS first_response_hours_p50,
               quantile_cont(first_response_hours, 0.9) AS first_response_hours_p90,
               count(*) FILTER (WHERE next_complaint_30d) AS next_complaint_30d,
               count(*) FILTER (WHERE is_repeat_complainer) AS repeat_flag_delivered,
               count(*) FILTER (WHERE prior_complaint_90d) AS prior_complaint_90d,
               count(*) FILTER (WHERE is_repeat_complainer AND prior_complaint_90d) AS repeat_flag_confirmed,
               count(claimed_amount) AS with_claimed_amount,
               min(event_date) AS first_date, max(event_date) AS last_date,
               date_diff('day', min(event_date), max(event_date)) + 1 AS window_days
        {DISPUTES}""")
    n = p["complaints"]
    p["sla_breach_rate"] = ratio(p["sla_breached"], n)
    p["escalation_rate"] = ratio(p["escalated"], n)
    p["repeat_30d_rate"] = ratio(p["next_complaint_30d"], n)
    p["status_mix"] = rows(con, f"SELECT status, count(*) AS complaints {DISPUTES} GROUP BY 1 ORDER BY 2 DESC, 1")
    p["channel_mix"] = rows(con, f"SELECT reception_channel AS channel, count(*) AS complaints {DISPUTES} "
                                 "GROUP BY 1 ORDER BY 2 DESC, 1")
    for mix in (p["status_mix"], p["channel_mix"]):
        for r in mix:
            r["share"] = ratio(r["complaints"], n)
    return p


def fcr_by_reason(con: duckdb.DuckDBPyConnection) -> list[dict]:
    out = rows(con, """
        SELECT reason_category, contact_reason, interactions, fcr_denominator, resolved_first_contact, fcr_rate,
               escalated, escalation_rate, requires_followup, with_duration, duration_p50_seconds,
               duration_p90_seconds, duration_mean_seconds
        FROM gold.interaction_outcomes WHERE dimension = 'overall'
        ORDER BY contact_reason = '(all reasons)', fcr_rate, contact_reason""")
    for r in out:
        r["fcr_ci95"] = wilson(r["resolved_first_contact"], r["fcr_denominator"])
    return out


def by_reason_category(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """FCR and mean handling time per reason category (additive counts, duration mean weighted by its n)."""
    return rows(con, """
        SELECT reason_category, sum(interactions)::BIGINT AS interactions,
               sum(resolved_first_contact)::BIGINT AS resolved_first_contact,
               sum(fcr_denominator)::BIGINT AS fcr_denominator,
               sum(resolved_first_contact) / sum(fcr_denominator) AS fcr_rate,
               sum(with_duration)::BIGINT AS with_duration,
               sum(duration_mean_seconds * with_duration) / sum(with_duration) AS duration_mean_seconds
        FROM gold.interaction_outcomes
        WHERE dimension = 'overall' AND contact_reason <> '(all reasons)'
        GROUP BY 1 ORDER BY fcr_rate, 1""")


def interaction_channel_mix(con: duckdb.DuckDBPyConnection) -> list[dict]:
    out = rows(con, """
        SELECT dimension_value AS channel, interactions FROM gold.interaction_outcomes
        WHERE contact_reason = '(all reasons)' AND dimension = 'channel' ORDER BY 2 DESC, 1""")
    total = sum(r["interactions"] for r in out)
    for r in out:
        r["share"] = ratio(r["interactions"], total)
    return out


def breakdowns(con: duckdb.DuckDBPyConnection) -> dict[str, list[dict]]:
    """Disputes by customer country and segment, with the customer base as a second denominator."""
    out = {}
    for dim, col in (("country", "customer_country"), ("segment", "customer_segment")):
        base = "country" if dim == "country" else "segment"
        out[dim] = rows(con, f"""
            WITH d AS (
                SELECT {col} AS value, count(*) AS complaints,
                       count(*) FILTER (WHERE sla_breached) AS sla_breached,
                       count(*) FILTER (WHERE is_escalated) AS escalated,
                       count(resolution_days) AS with_resolution_days,
                       quantile_cont(resolution_days, 0.5) AS resolution_days_p50,
                       quantile_cont(resolution_days, 0.9) AS resolution_days_p90,
                       count(*) FILTER (WHERE next_complaint_30d) AS next_complaint_30d
                {DISPUTES} GROUP BY 1),
            c AS (SELECT {base} AS value, count(*) AS customers FROM gold.customer_profile GROUP BY 1),
            f AS (SELECT dimension_value AS value, sum(resolved_first_contact) AS fcr_resolved,
                         sum(fcr_denominator) AS fcr_denominator
                  FROM gold.interaction_outcomes
                  WHERE reason_category = 'Complaint' AND dimension = 'customer_{dim}' GROUP BY 1)
            SELECT d.*, c.customers, f.fcr_resolved AS complaint_fcr_resolved,
                   f.fcr_denominator AS complaint_fcr_denominator
            FROM d LEFT JOIN c USING (value) LEFT JOIN f USING (value) ORDER BY complaints DESC, value""")
        for r in out[dim]:
            r["disputes_per_1000_customers"] = round(1000 * r["complaints"] / r["customers"], 2) if r["customers"] else None
            r["sla_breach_rate"] = ratio(r["sla_breached"], r["complaints"])
            r["escalation_rate"] = ratio(r["escalated"], r["complaints"])
            r["complaint_contact_fcr"] = ratio(r["complaint_fcr_resolved"], r["complaint_fcr_denominator"])
    return out


def _by(con: duckdb.DuckDBPyConnection, column: str, source: str, disputes_only: bool) -> list[dict]:
    where = f"source_system = '{source}'" + (" AND is_unrecognized_charge" if disputes_only else "")
    out = rows(con, f"SELECT {column} AS bucket, sum(contacts)::BIGINT AS contacts FROM gold.demand_by_hour "
                    f"WHERE {where} GROUP BY 1 ORDER BY 1")
    total = sum(r["contacts"] for r in out)
    for r in out:
        r["share"] = ratio(r["contacts"], total)
    return out


def daily_stats(con: duckdb.DuckDBPyConnection, source: str, disputes_only: bool, by_country: bool) -> list[dict]:
    """Per calendar day (days with zero contacts included), mean, p50, p95 and max of contacts."""
    where = f"source_system = '{source}'" + (" AND is_unrecognized_charge" if disputes_only else "")
    group = "country" if by_country else "'all'"
    return rows(con, f"""
        WITH bounds AS (SELECT min(event_date) AS lo, max(event_date) AS hi FROM gold.demand_by_day WHERE {where}),
        days AS (SELECT CAST(unnest(generate_series(lo, hi, INTERVAL 1 DAY)) AS DATE) AS event_date FROM bounds),
        groups AS (SELECT DISTINCT {group} AS grp FROM gold.demand_by_day WHERE {where}),
        counts AS (SELECT {group} AS grp, event_date, sum(contacts) AS contacts FROM gold.demand_by_day
                   WHERE {where} GROUP BY ALL),
        dense AS (SELECT g.grp, d.event_date, coalesce(c.contacts, 0) AS contacts FROM groups g CROSS JOIN days d
                  LEFT JOIN counts c ON c.grp = g.grp AND c.event_date = d.event_date)
        SELECT grp AS country, count(*) AS days, sum(contacts)::BIGINT AS contacts,
               round(avg(contacts), 2) AS mean_per_day, quantile_cont(contacts, 0.5) AS p50_per_day,
               quantile_cont(contacts, 0.95) AS p95_per_day, max(contacts)::BIGINT AS max_per_day
        FROM dense GROUP BY 1 ORDER BY contacts DESC, 1""")


def demand(con: duckdb.DuckDBPyConnection) -> dict:
    weeks = one(con, "SELECT (date_diff('day', min(event_date), max(event_date)) + 1) / 7.0 AS weeks "
                     "FROM gold.demand_by_day WHERE source_system = 'complaints' AND is_unrecognized_charge")["weeks"]
    peak = one(con, """
        SELECT iso_dow, hour_of_day, sum(contacts)::BIGINT AS contacts FROM gold.demand_by_hour
        WHERE source_system = 'complaints' AND is_unrecognized_charge GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 1""")
    peak["mean_per_week"] = round(peak["contacts"] / weeks, 2) if weeks else None
    return {
        "disputes_by_hour": _by(con, "hour_of_day", "complaints", True),
        "disputes_by_weekday": _by(con, "iso_dow", "complaints", True),
        "disputes_by_country": _by(con, "country", "complaints", True),
        "interactions_by_hour": _by(con, "hour_of_day", "interactions", False),
        "interactions_by_weekday": _by(con, "iso_dow", "interactions", False),
        "disputes_daily": daily_stats(con, "complaints", True, by_country=False),
        "disputes_daily_by_country": daily_stats(con, "complaints", True, by_country=True),
        "interactions_daily": daily_stats(con, "interactions", False, by_country=False),
        "busiest_dispute_weekday_hour": peak,
        "weeks_in_window": round(weeks, 2) if weeks else None,
    }


def spread(values: list[int]) -> dict:
    """Max/min ratio and coefficient of variation: how far a distribution is from flat."""
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return {"n_buckets": len(values), "min": min(values), "max": max(values),
            "max_over_min": round(max(values) / min(values), 3) if min(values) else None,
            "coefficient_of_variation": round(sd / mean, 4) if mean else None}
