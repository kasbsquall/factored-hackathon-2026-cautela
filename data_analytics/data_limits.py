"""Diagnostics that show where the supplied data is uniform, templated or unlinkable.

These bound what the analysis can claim: a flat distribution cannot rank workflows by outcome, and a templated
text field cannot train or test language understanding. Silver is read here on purpose, because the text and
link columns are not carried into gold.
"""

from __future__ import annotations

import duckdb

from data_analytics.figures import one, rows, spread


def _labeled_type_counts(con: duckdb.DuckDBPyConnection) -> list[int]:
    """Complaint types that carry a subcategory (the ones a workflow could be built on)."""
    return [r["complaints"] for r in rows(con, "SELECT complaints FROM gold.workflow_selection "
                                               "WHERE complaint_type NOT LIKE '%(no subcategory)'")]


def diagnostics(con: duckdb.DuckDBPyConnection) -> dict:
    d: dict = {}
    types = _labeled_type_counts(con)
    d["complaint_type_volume"] = spread(types) if types else None
    sla = rows(con, "SELECT sla_breach_rate FROM gold.workflow_selection")
    d["sla_breach_rate_range"] = ([round(min(r["sla_breach_rate"] for r in sla), 4),
                                   round(max(r["sla_breach_rate"] for r in sla), 4)] if sla else None)
    d["sla_vs_resolution_days"] = rows(con, """
        SELECT sla_breached, count(resolution_days) AS with_resolution_days,
               quantile_cont(resolution_days, 0.5) AS resolution_days_p50,
               round(avg(resolution_days), 2) AS resolution_days_mean
        FROM gold.complaint_facts GROUP BY 1 ORDER BY 1""")
    d["resolution_days_by_status"] = rows(con, """
        SELECT status, count(*) AS complaints, count(resolution_days) AS with_resolution_days
        FROM gold.complaint_facts GROUP BY 1 ORDER BY 2 DESC, 1""")
    hours = [r["c"] for r in rows(con, "SELECT sum(contacts) AS c FROM gold.demand_by_hour "
                                       "WHERE source_system = 'interactions' GROUP BY hour_of_day")]
    d["interaction_hour_of_day"] = spread(hours) if hours else None
    d["repeat_flag"] = one(con, """
        SELECT count(*) AS complaints, count(*) FILTER (WHERE is_repeat_complainer) AS flagged,
               count(*) FILTER (WHERE prior_complaint_90d) AS with_prior_complaint_90d,
               count(*) FILTER (WHERE is_repeat_complainer AND prior_complaint_90d) AS flagged_and_confirmed
        FROM gold.complaint_facts""")
    d["dispute_descriptions"] = one(con, """
        SELECT count(*) AS complaints, count(DISTINCT description) AS distinct_descriptions
        FROM silver.complaints WHERE category = 'Cargo no reconocido' OR subcategory = 'Cargo no reconocido'""")
    d["transcripts"] = one(con, """
        SELECT count(*) AS transcripts, count(DISTINCT full_text) AS distinct_full_text,
               count(DISTINCT customer_text) AS distinct_customer_text,
               count(DISTINCT detected_intents) AS distinct_intents
        FROM silver.call_transcripts""")
    d["complaint_links"] = one(con, """
        SELECT count(*) AS complaints, count(origin_interaction_id) AS with_origin_interaction
        FROM silver.complaints""")
    d["dispute_currency_vs_country"] = rows(con, """
        SELECT customer_country, currency, count(*) AS complaints FROM gold.complaint_facts
        WHERE is_unrecognized_charge AND currency IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""")
    d["transactions"] = one(con, """
        SELECT count(*) AS transactions, count(*) FILTER (WHERE is_fraud) AS fraud_labeled,
               count(*) FILTER (WHERE customer_country = 'Mexico') AS mexico_transactions,
               count(*) FILTER (WHERE customer_country = 'Mexico' AND currency = 'USD') AS mexico_usd
        FROM gold.dispute_policy_inputs""")
    tx = d["transactions"]
    tx["fraud_rate"] = round(tx["fraud_labeled"] / tx["transactions"], 6) if tx["transactions"] else None
    d["registration_branch"] = one(con, """
        SELECT count(*) AS customers, count(*) FILTER (WHERE NOT registration_branch_known) AS orphan_branch
        FROM gold.customer_profile""")
    return d
