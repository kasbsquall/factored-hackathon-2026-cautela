"""The two policy thresholds that send a charge to a person, measured over the whole transaction window.

agent/policy/rules.yaml sets SYN-AMOUNT-001 (USD amount at or above a threshold) and SYN-FRAUD-001 (fraud score at
or above a threshold, or the fraud flag). Both values were first chosen on one month of data. This module reads the
thresholds, the disputable statuses and the fixed FX rates from rules.yaml itself (never retyped), converts amounts
the way the policy engine does, and reports what share of disputable charges each rule routes to a person, plus
the one-month figures the rules.yaml comments quote so they can be reproduced.
"""

from __future__ import annotations

import duckdb

from agent.policy.engine import load_rules
from agent.tools.impl import NON_CHARGE_TYPES
from data_analytics.figures import one, ratio, rows

QUANTILES = (0.5, 0.75, 0.9, 0.95, 0.99)
EDA_MONTH = "2024-03"  # the month the rules.yaml comments quote


def _usd_sql(rules: dict) -> tuple[str, list]:
    """SQL for the USD amount the engine uses: amount_usd, else amount when USD, else amount / fixed rate."""
    fx = rules["fx_rates"]["units_per_usd"]
    cases = " ".join("WHEN currency = ? THEN round(amount / ?, 2)" for _ in fx)
    params = [v for cur, entry in fx.items() for v in (cur, float(entry["rate"]))]
    return f"coalesce(amount_usd, CASE WHEN currency = 'USD' THEN amount {cases} END)", params


def _disputable(rules: dict) -> tuple[str, list]:
    statuses = next(r["statuses"] for r in rules["disputable_status"] if r["id"] == "SYN-STATUS-001")
    return ("transaction_status IN (SELECT unnest(?)) AND transaction_type NOT IN (SELECT unnest(?))",
            [list(statuses), list(NON_CHARGE_TYPES)])


def amount_rule(con: duckdb.DuckDBPyConnection, rules: dict) -> dict:
    threshold = float(rules["amount_review"]["threshold_usd"])
    usd, usd_p = _usd_sql(rules)
    where, where_p = _disputable(rules)
    qs = ", ".join(f"quantile_cont(usd, {q}) AS p{round(q * 100)}" for q in QUANTILES)
    overall = rows(con, f"""
        WITH c AS (SELECT transaction_type, {usd} AS usd FROM gold.dispute_policy_inputs WHERE {where})
        SELECT count(*) AS charges, count(usd) AS with_usd, count(*) FILTER (WHERE usd >= ?) AS at_or_above,
               {qs} FROM c""", usd_p + where_p + [threshold])[0]
    by_type = rows(con, f"""
        WITH c AS (SELECT transaction_type, {usd} AS usd FROM gold.dispute_policy_inputs WHERE {where})
        SELECT transaction_type, count(*) AS charges, count(*) FILTER (WHERE usd >= ?) AS at_or_above,
               quantile_cont(usd, 0.9) AS p90
        FROM c GROUP BY 1 ORDER BY 2 DESC, 1""", usd_p + where_p + [threshold])
    monthly = rows(con, """
        SELECT strftime(transaction_date, '%Y-%m') AS month, count(amount_usd) AS purchases_with_usd,
               quantile_cont(amount_usd, 0.9) AS p90
        FROM gold.dispute_policy_inputs WHERE transaction_type = 'Purchase' GROUP BY 1 ORDER BY 1""")
    eda = one(con, f"""
        SELECT count(*) AS transactions, count(*) FILTER (WHERE transaction_type = 'Purchase') AS purchases,
               count(amount_usd) FILTER (WHERE transaction_type = 'Purchase') AS purchases_with_usd,
               quantile_cont(amount_usd, 0.9) FILTER (WHERE transaction_type = 'Purchase') AS purchase_p90_usd
        FROM gold.dispute_policy_inputs WHERE strftime(transaction_date, '%Y-%m') = '{EDA_MONTH}'""")
    for r in (overall, *by_type):
        r["share_at_or_above"] = ratio(r["at_or_above"], r["charges"])
    full = [m for m in monthly if m["purchases_with_usd"]]
    return {"rule_id": rules["amount_review"]["id"], "threshold_usd": threshold, "overall": overall,
            "by_type": by_type, "eda_month": {"month": EDA_MONTH, **eda},
            "purchase_p90_by_month": {"months": len(full), "min": min((m["p90"] for m in full), default=None),
                                      "max": max((m["p90"] for m in full), default=None)}}


def fraud_rule(con: duckdb.DuckDBPyConnection, rules: dict) -> dict:
    fraud = rules["fraud_escalation"]
    t = float(fraud["fraud_score_at_least"])
    where, where_p = _disputable(rules)
    by_label = rows(con, """
        SELECT is_fraud, count(*) AS transactions, count(fraud_score) AS with_score, max(fraud_score) AS max_score,
               quantile_cont(fraud_score, 0.5) AS p50_score, count(*) FILTER (WHERE fraud_score >= ?) AS at_or_above
        FROM gold.dispute_policy_inputs GROUP BY 1 ORDER BY 1""", [t])
    unflagged = next((r for r in by_label if r["is_fraud"] is False), None)
    flagged = next((r for r in by_label if r["is_fraud"] is True), None)
    gap = None
    if unflagged and unflagged["max_score"] is not None:
        gap = one(con, f"""
            SELECT count(*) FILTER (WHERE is_fraud AND fraud_score > {float(unflagged['max_score'])})
                       AS flagged_above_unflagged_max
            FROM gold.dispute_policy_inputs""")
    routed = rows(con, f"""
        SELECT count(*) AS charges,
               count(*) FILTER (WHERE fraud_score >= ? OR is_fraud) AS routed,
               count(*) FILTER (WHERE fraud_score >= ? AND NOT is_fraud) AS routed_by_score_only
        FROM gold.dispute_policy_inputs WHERE {where}""", [t, t] + where_p)[0]
    routed["share_routed"] = ratio(routed["routed"], routed["charges"])
    eda = rows(con, f"""
        SELECT is_fraud, count(*) AS transactions, max(fraud_score) AS max_score,
               count(*) FILTER (WHERE fraud_score >= ?) AS at_or_above
        FROM gold.dispute_policy_inputs WHERE strftime(transaction_date, '%Y-%m') = '{EDA_MONTH}'
        GROUP BY 1 ORDER BY 1""", [t])
    return {"rule_id": fraud["id"], "score_at_least": t, "by_label": by_label,
            "unflagged_max_score": unflagged and unflagged["max_score"],
            "flagged_with_score": flagged and flagged["with_score"],
            "flagged_above_unflagged_max": gap and gap["flagged_above_unflagged_max"],
            "disputable": routed, "eda_month": {"month": EDA_MONTH, "by_label": eda}}


def combined(con: duckdb.DuckDBPyConnection, rules: dict) -> dict:
    """Disputable charges either threshold sends to a person (claim windows depend on the day and are left out)."""
    usd, usd_p = _usd_sql(rules)
    where, where_p = _disputable(rules)
    a, f = float(rules["amount_review"]["threshold_usd"]), float(rules["fraud_escalation"]["fraud_score_at_least"])
    out = rows(con, f"""
        WITH c AS (SELECT {usd} AS usd, fraud_score, is_fraud FROM gold.dispute_policy_inputs WHERE {where})
        SELECT count(*) AS charges, count(*) FILTER (WHERE usd >= ? OR fraud_score >= ? OR is_fraud) AS routed,
               count(*) FILTER (WHERE usd IS NULL) AS without_usd
        FROM c""", usd_p + where_p + [a, f])[0]
    out["share_routed"] = ratio(out["routed"], out["charges"])
    return out


def threshold_analysis(con: duckdb.DuckDBPyConnection) -> dict:
    rules = load_rules()
    return {"policy_file": "agent/policy/rules.yaml", "policy_version": rules.get("version"),
            "base": "gold.dispute_policy_inputs, every transaction in the window with a disputable status that is "
                    "not a deposit",
            "disputable_statuses": next(r["statuses"] for r in rules["disputable_status"]
                                        if r["id"] == "SYN-STATUS-001"),
            "non_charge_types": list(NON_CHARGE_TYPES),
            "amount": amount_rule(con, rules), "fraud": fraud_rule(con, rules), "either": combined(con, rules)}
