"""Cost per resolution and the savings range for the unrecognized-charge workflow.

Every input carries a `kind`:
  measured     computed from the warehouse by this package (query named in `source`)
  cited        read from another committed team artifact (file named in `source`); offline, not production
  assumption   not in the data; a modeling choice or a placeholder the bank must replace
  not_defined  needed for a full cost but absent; left out of the arithmetic and said so

Scope: human intake handling only. Back-office investigation time and hosting are not in the data, so they are
left out. The projection is a range over three independent choices:
  automation rate   conservative: end-to-end safe automated resolution of the deployed configuration
                    (eval/results.json); optimistic: the decision component alone (ml/reports/results.json)
  channel reach     the App and Web share of dispute intake (measured); and, as an assumption, that share plus the
                    call-center disputes if the bank routes them to the same flow
  handling time     low, central and high human minutes per dispute (measured proxies and one assumption)
A conversation the bot does not resolve safely still costs its LLM tokens and then the full human handling time.
All outputs are projections from offline measurements, never measured production savings.
"""

from __future__ import annotations

import json
from pathlib import Path

# Illustrative placeholders, not sourced: the data has no labor cost. Replace with the bank's loaded hourly cost.
ILLUSTRATIVE_HOURLY_COST_USD = (5.0, 10.0, 20.0)
CENTRAL_HOURLY_COST_USD = 10.0
# The public demo runs the learned disposition with gpt-6-luna for extraction and replies (deploy/README.md, "The
# model"); in eval/results.json that is configuration `llm`, configuration (c) of eval/report.md.
DEPLOYED_EVAL_CONFIG = "llm"


def ml_rates(results_path: str | Path) -> dict:
    """Safe automated resolution of the proposed decision component, read from ml/reports/results.json."""
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    name = data["proposed_system"]
    s = data["systems"][name]["summary"]
    n = s["n_cases"]
    sar = s["safe_automated_resolution_rate"]
    return {"system": name, "data_version": data["data_version"], "split": data["split"], "n_cases": n,
            "safe_automated_resolution_rate": sar["value"], "safe_automated_resolution_ci95": sar["ci95"],
            "safe_automated_resolutions": round(sar["value"] * n), "automation_attempted_share":
            s["automation_attempted_share"], "unsafe": s["unsafe"], "source": Path(results_path).as_posix()}


def eval_rates(eval_path: str | Path, config: str = DEPLOYED_EVAL_CONFIG) -> dict:
    """End-to-end rates and measured LLM spend of one configuration, read from eval/results.json."""
    data = json.loads(Path(eval_path).read_text(encoding="utf-8"))
    c = data["configs"][config]
    s, cost = c["summary"], c["cost"]
    sar, attempted = s["safe_automated_resolution"], s["automation_attempted"]
    total, conversations = float(cost["llm_cost_usd_total"]), s["conversations"]
    return {"config": config, "run_id": c["run_id"], "description": c.get("description"),
            "source": Path(eval_path).as_posix(), "conversations": conversations,
            "safe_automated_resolution_rate": sar["rate"], "safe_automated_resolutions": sar["k"],
            "in_scope_conversations": sar["n"], "safe_automated_resolution_ci95": sar.get("ci95"),
            "automation_attempted_share": attempted["rate"], "llm_calls": cost.get("llm_calls"),
            "llm_cost_usd_total": total,
            "llm_usd_per_conversation": round(total / conversations, 7) if conversations else None,
            "llm_usd_per_safe_automated_resolution": round(total / sar["k"], 7) if sar["k"] else None,
            "reported_cost": cost}


def _inp(value, unit: str, kind: str, source: str, note: str = "") -> dict:
    return {"value": value, "unit": unit, "kind": kind, "source": source, "note": note}


def _handling_inputs(profile: dict, categories: list[dict]) -> dict:
    by_reason = {r["reason_category"]: r for r in categories}
    complaint, transactional = by_reason.get("Complaint"), by_reason.get("Transactional")
    return {
        "handling_seconds_complaint_contact": _inp(
            complaint and round(complaint["duration_mean_seconds"], 1), "seconds", "measured",
            "gold.interaction_outcomes, reason Complaint, mean duration_seconds",
            f"proxy: complaints cannot be linked to interactions; n = {complaint and complaint['with_duration']}"),
        "handling_seconds_transactional_contact": _inp(
            transactional and round(transactional["duration_mean_seconds"], 1), "seconds", "measured",
            "gold.interaction_outcomes, reason Transactional, mean duration_seconds",
            f"lower proxy; n = {transactional and transactional['with_duration']}"),
        "complaint_contact_fcr": _inp(complaint and round(complaint["fcr_rate"], 4), "share", "measured",
                                      "gold.interaction_outcomes, reason Complaint",
                                      complaint and f"{complaint['resolved_first_contact']} / "
                                                    f"{complaint['fcr_denominator']}"),
        "contacts_per_dispute_floor": _inp(1, "contacts per dispute", "assumption", "modeling choice",
                                           "one intake contact per complaint"),
        "contacts_per_dispute_upper": _inp(
            round(1 / complaint["fcr_rate"], 3) if complaint and complaint["fcr_rate"] else None,
            "contacts per dispute", "assumption", "1 / complaint_contact_fcr",
            "every unresolved contact retries with the same FCR (geometric)"),
    }


def build_inputs(profile: dict, categories: list[dict], ml: dict, ev: dict, reach: dict) -> dict:
    days = profile["window_days"]
    return {
        "disputes_in_window": _inp(profile["complaints"], "complaints", "measured", "gold.complaint_facts",
                                   f"{profile['first_date']} to {profile['last_date']}"),
        "window_days": _inp(days, "days", "measured", "gold.complaint_facts"),
        "disputes_per_year": _inp(round(profile["complaints"] / days * 365, 1), "complaints per year", "measured",
                                  "disputes_in_window / window_days * 365"),
        **_handling_inputs(profile, categories),
        "chat_eligible_share": _inp(round(reach["chat_eligible_share"], 4), "share of disputes", "measured",
                                    "gold.complaint_facts, reception_channel App or Web",
                                    f"{reach['chat_eligible']} / {reach['complaints']}"),
        "call_center_share": _inp(round(reach["call_center_share"], 4), "share of disputes", "measured",
                                  "gold.complaint_facts, reception_channel Call Center",
                                  f"{reach['call_center']} / {reach['complaints']}"),
        "call_center_routed_to_flow": _inp(True, "yes/no", "assumption", "scenario",
                                           "only in the second reach scenario: the bank sends call-center disputes "
                                           "to the same flow; nothing in the data measures that they would use it"),
        "safe_automated_resolution_conservative": _inp(
            ev["safe_automated_resolution_rate"], "share of in-scope conversations", "cited", ev["source"],
            f"end to end, configuration `{ev['config']}` (the deployed one), run {ev['run_id']}, "
            f"{ev['safe_automated_resolutions']} / {ev['in_scope_conversations']} simulated conversations"),
        "safe_automated_resolution_optimistic": _inp(
            ml["safe_automated_resolution_rate"], "share of cases", "cited", ml["source"],
            f"decision component only, {ml['n_cases']} generated cases of the {ml['split']} split, the split used "
            "for error analysis"),
        "llm_usd_per_conversation": _inp(
            ev["llm_usd_per_conversation"], "USD per conversation", "cited", ev["source"],
            f"llm_cost_usd_total {ev['llm_cost_usd_total']} / {ev['conversations']} conversations of run "
            f"{ev['run_id']}; provider tokens times list price, not an invoice"),
        "llm_usd_per_safe_automated_resolution": _inp(
            ev["llm_usd_per_safe_automated_resolution"], "USD per safe automated resolution", "cited", ev["source"],
            f"llm_cost_usd_total / {ev['safe_automated_resolutions']} safe automated resolutions"),
        "failed_attempt_human_minutes": _inp("full handling time", "minutes", "assumption", "conservative choice",
                                             "a conversation the bot does not resolve safely costs its tokens and "
                                             "then the whole human handling time; a structured handoff may shorten "
                                             "the human contact, not measured"),
        "hosting_usd_per_conversation": _inp(None, "USD per conversation", "not_defined", "not measured",
                                             "compute and hosting are excluded; the break-even column bounds them"),
        "back_office_minutes_per_dispute": _inp(None, "minutes", "not_defined", "not in the data",
                                                "investigation and chargeback effort are excluded"),
        "hourly_cost_usd": _inp(list(ILLUSTRATIVE_HOURLY_COST_USD), "USD per agent-hour", "assumption",
                                "illustrative placeholder", "not sourced; replace with the bank's loaded cost"),
    }


def handling_scenarios(inputs: dict) -> list[tuple[str, float, float]]:
    low_s = inputs["handling_seconds_transactional_contact"]["value"]
    mid_s = inputs["handling_seconds_complaint_contact"]["value"]
    upper = inputs["contacts_per_dispute_upper"]["value"]
    out = []
    if low_s:
        out.append(("low: transactional contact time, one contact", low_s, 1))
    if mid_s:
        out.append(("central: complaint contact time, one contact", mid_s, 1))
        if upper:
            out.append(("high: complaint contact time, repeat contacts at complaint FCR", mid_s, upper))
    return out


def _money(minutes: float, sar: float, bot: float, routed: float) -> list[dict]:
    out = []
    for rate in ILLUSTRATIVE_HOURLY_COST_USD:
        human = minutes / 60 * rate
        out.append({"hourly_cost_usd": rate, "human_cost_per_dispute_usd": round(human, 2),
                    "cost_per_routed_dispute_with_bot_usd": round(bot + (1 - sar) * human, 4),
                    "net_saving_per_routed_dispute_usd": round(sar * human - bot, 4),
                    "net_savings_per_year_usd": round(routed * (sar * human - bot)),
                    "break_even_bot_usd_per_conversation": round(sar * human, 4)})
    return out


def projections(inputs: dict, cc_hours_per_year: float | None) -> list[dict]:
    per_year = inputs["disputes_per_year"]["value"]
    bot = inputs["llm_usd_per_conversation"]["value"] or 0.0
    chat = inputs["chat_eligible_share"]["value"]
    reaches = (("App and Web disputes (measured share)", chat),
               ("App, Web and call-center disputes (assumption: call center routed to the flow)",
                round(chat + inputs["call_center_share"]["value"], 4)))
    rates = (("conservative", inputs["safe_automated_resolution_conservative"]["value"]),
             ("optimistic", inputs["safe_automated_resolution_optimistic"]["value"]))
    out = []
    for name, seconds, contacts in handling_scenarios(inputs):
        minutes = seconds * contacts / 60
        for reach, share in reaches:
            routed = per_year * share
            for rate_name, sar in rates:
                saved = routed * sar * minutes / 60
                out.append({"scenario": name, "reach": reach, "reach_share": share, "rate": rate_name,
                            "safe_automated_resolution_rate": sar, "human_minutes_per_dispute": round(minutes, 2),
                            "disputes_routed_per_year": round(routed, 1),
                            "safe_automated_per_year": round(routed * sar, 1),
                            "handoffs_per_year": round(routed * (1 - sar), 1),
                            "human_hours_routed_per_year": round(routed * minutes / 60, 1),
                            "hours_saved_per_year": round(saved, 1),
                            "human_hours_left_per_year": round(routed * (1 - sar) * minutes / 60, 1),
                            "share_of_contact_center_hours": round(saved / cc_hours_per_year, 5)
                            if cc_hours_per_year else None,
                            "llm_usd_per_year": round(routed * bot, 2),
                            "money": _money(minutes, sar, bot, routed)})
    return out


def cost_per_resolution(inputs: dict) -> dict:
    """Human cost of one dispute intake against the measured LLM cost of one safe automated resolution."""
    central = next((s for s in handling_scenarios(inputs) if s[0].startswith("central")), None)
    minutes = central[1] * central[2] / 60 if central else None
    sar = inputs["safe_automated_resolution_conservative"]["value"]
    bot = inputs["llm_usd_per_conversation"]["value"] or 0.0
    human = {str(r): round(minutes / 60 * r, 2) for r in ILLUSTRATIVE_HOURLY_COST_USD} if minutes else None
    blended = ({str(r): round(bot + (1 - sar) * minutes / 60 * r, 4) for r in ILLUSTRATIVE_HOURLY_COST_USD}
               if minutes else None)
    return {"scenario": central and central[0], "human_minutes_per_dispute": minutes and round(minutes, 2),
            "human_usd_per_dispute_by_hourly_cost": human,
            "llm_usd_per_safe_automated_resolution": inputs["llm_usd_per_safe_automated_resolution"]["value"],
            "llm_usd_per_conversation": bot,
            "cost_per_routed_dispute_with_bot_by_hourly_cost": blended,
            "rate": "conservative", "hosting": "not defined",
            "source_llm": inputs["llm_usd_per_conversation"]["source"]}


def cost_model(profile: dict, categories: list[dict], ml: dict, ev: dict, reach: dict,
               cc_hours_per_year: float | None = None) -> dict:
    inputs = build_inputs(profile, categories, ml, ev, reach)
    return {"scope": "human intake handling of unrecognized-charge complaints; projection from offline results",
            "formula": {
                "human_minutes_per_dispute": "handling_seconds * contacts_per_dispute / 60",
                "disputes_routed_per_year": "disputes_per_year * reach_share",
                "hours_saved_per_year": "disputes_routed_per_year * safe_automated_resolution * "
                                        "human_minutes_per_dispute / 60",
                "cost_per_routed_dispute_with_bot": "llm_usd_per_conversation + (1 - safe_automated_resolution) * "
                                                    "human_cost_per_dispute",
                "net_savings_per_year": "disputes_routed_per_year * (safe_automated_resolution * "
                                        "human_cost_per_dispute - llm_usd_per_conversation)",
                "break_even_bot_usd_per_conversation": "safe_automated_resolution * human_cost_per_dispute"},
            "inputs": inputs, "cost_per_resolution": cost_per_resolution(inputs),
            "projections": projections(inputs, cc_hours_per_year)}
