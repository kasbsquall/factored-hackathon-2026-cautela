"""insights.json: the decision figures the /insights page shows, in one small file with units and sources.

Everything here is selected from the collected evidence (`run.collect`); nothing is computed that the report does not
also print. Money uses the central placeholder hourly cost and says so in `assumptions`.
"""

from __future__ import annotations

from data_analytics.cost_model import CENTRAL_HOURLY_COST_USD


def _money_at(p: dict, rate: float) -> dict:
    return next(m for m in p["money"] if m["hourly_cost_usd"] == rate)


def projection_range(cm: dict) -> dict:
    rows = []
    for p in cm["projections"]:
        m = _money_at(p, CENTRAL_HOURLY_COST_USD)
        rows.append({"handling": p["scenario"].split(":")[0], "reach": p["reach"], "reach_share": p["reach_share"],
                     "rate": p["rate"], "rate_label": p["rate_label"],
                     "safe_automated_resolution_rate": p["safe_automated_resolution_rate"],
                     "disputes_routed_per_year": p["disputes_routed_per_year"],
                     "hours_saved_per_year": p["hours_saved_per_year"],
                     "human_hours_left_per_year": p["human_hours_left_per_year"],
                     "handoffs_per_year": p["handoffs_per_year"],
                     "share_of_contact_center_hours": p["share_of_contact_center_hours"],
                     "llm_usd_per_year": p["llm_usd_per_year"],
                     "net_savings_per_year_usd": m["net_savings_per_year_usd"]})
    central = [r for r in rows if r["handling"] == "central"]
    narrow = min((x["reach_share"] for x in central), default=None)
    wide = max((x["reach_share"] for x in central), default=None)
    return {"units": {"hours_saved_per_year": "agent-hours per year", "net_savings_per_year_usd": "USD per year",
                      "reach_share": "share of dispute complaints", "llm_usd_per_year": "USD per year"},
            "hourly_cost_usd": CENTRAL_HOURLY_COST_USD,
            "headline": {
                "conservative_chat_only": next((r for r in central if r["rate"] == "conservative"
                                                and r["reach_share"] == narrow), None),
                "optimistic_with_call_center": next((r for r in central if r["rate"] == "optimistic"
                                                     and r["reach_share"] == wide), None)},
            "rows": rows}


def insights(ev: dict) -> dict:
    p, cm, rank, th, sat = ev["provenance"], ev["cost_model"], ev["workflow_ranking"], ev["thresholds"], \
        ev["satisfaction"]
    inputs = cm["inputs"]
    chosen = next(r for r in rank["rows"] if r["verifiable"] == "yes")
    amount, fraud = th["amount"], th["fraud"]
    return {
        "title": "Decision figures for the unrecognized-charge workflow",
        "provenance": {**p, "eval_source": ev["eval"]["source"], "eval_config": ev["eval"]["config"],
                       "eval_run_id": ev["eval"]["run_id"], "ml_source": ev["ml"]["source"],
                       "ml_data_version": ev["ml"]["data_version"],
                       "report": "data_analytics/reports/why-this-workflow.md"},
        "workflow_ranking": {
            "unit": "agent-hours per year", "formula": rank["formula"],
            "judgment_columns": rank["judgment_columns"],
            "chosen_rank": chosen["rank"], "candidates": len(rank["rows"]),
            "contact_center_agent_hours_per_year": rank["contact_center"]["agent_hours_per_year"],
            "rows": [{k: r[k] for k in ("rank", "workflow", "proxy", "contacts_per_year", "handling_seconds",
                                        "first_contact_resolution", "agent_hours_per_year",
                                        "unresolved_contact_hours_per_year", "verifiable", "record")}
                     for r in rank["rows"]],
            "dispute_record_coverage": chosen.get("record_coverage")},
        "cost_per_resolution": {**cm["cost_per_resolution"],
                                "units": {"human_usd_per_dispute_by_hourly_cost": "USD per dispute, keyed by USD "
                                                                                  "per agent-hour",
                                          "llm_usd_per_safe_automated_resolution": "USD per safe automated "
                                                                                   "resolution (LLM tokens only)",
                                          "llm_usd_per_conversation": "USD per conversation (LLM tokens only)",
                                          "cost_per_routed_dispute_with_bot_by_hourly_cost":
                                              "USD per dispute sent to the bot, failed attempts at full human cost"}},
        "projection_range": projection_range(cm),
        "handoff_queue": ev["handoff_queue"],
        "channel_reach": {k: ev["channel_reach"][k] for k in ("complaints", "chat_eligible_share",
                                                              "call_center_share", "chat_plus_call_center_share",
                                                              "digital_event_30d_total")},
        "thresholds": {
            "policy_version": th["policy_version"],
            "amount": {"rule_id": amount["rule_id"], "threshold_usd": amount["threshold_usd"],
                       "disputable_charges": amount["overall"]["charges"],
                       "share_routed": amount["overall"]["share_at_or_above"],
                       "percentiles_usd": {k: amount["overall"][k] for k in amount["overall"] if k.startswith("p")},
                       "eda_month": amount["eda_month"], "purchase_p90_by_month": amount["purchase_p90_by_month"]},
            "fraud": {"rule_id": fraud["rule_id"], "score_at_least": fraud["score_at_least"],
                      "unflagged_max_score": fraud["unflagged_max_score"],
                      "flagged_with_score": fraud["flagged_with_score"],
                      "flagged_above_unflagged_max": fraud["flagged_above_unflagged_max"],
                      "share_routed": fraud["disputable"]["share_routed"],
                      "routed_by_score_only": fraud["disputable"]["routed_by_score_only"],
                      "by_label": fraud["by_label"]},
            "either_share_routed": th["either"]["share_routed"],
            "units": {"share_routed": "share of disputable charges sent to a person", "threshold_usd": "USD"}},
        "satisfaction": {"survey_csat_by_first_contact_resolution": sat["surveys"]["by_first_contact_resolution"],
                         "survey_csat_scale": sat["surveys"]["scale"],
                         "survey_csat_by_reason": sat["surveys"]["by_reason"],
                         "dispute_resolution_satisfaction": sat["complaints"]["by_is_dispute"]},
        "assumptions": [
            f"USD {CENTRAL_HOURLY_COST_USD:g} per agent-hour is an illustrative placeholder "
            f"({inputs['hourly_cost_usd']['note']}).",
            inputs["call_center_routed_to_flow"]["note"],
            inputs["failed_attempt_human_minutes"]["note"],
            "Handling time per dispute is the Complaint contact-reason mean (complaints cannot be linked to contacts).",
            "The workflow-to-contact-reason mapping and the verifiable column are team judgments.",
        ],
    }
