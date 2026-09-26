"""Cost-per-resolution model for the unrecognized-charge workflow.

Every input carries a `kind`:
  measured     computed from the warehouse by this package (query named in `source`)
  cited        read from another team artifact (file named in `source`); offline, not production
  assumption   not in the data; a modeling choice or a placeholder the bank must replace
  not_defined  needed for a full cost but absent; left out of the arithmetic and said so

Scope: human intake handling only. Back-office investigation time is not in the data, so savings are a floor on
human effort under the stated assumptions, and the money figures depend on an hourly cost that is a placeholder.
All outputs are projections from offline measurements, never measured production savings.
"""

from __future__ import annotations

import json
from pathlib import Path

# Illustrative placeholders, not sourced: the data has no labor cost. Replace with the bank's loaded hourly cost.
ILLUSTRATIVE_HOURLY_COST_USD = (5.0, 10.0, 20.0)


def ml_rates(results_path: str | Path) -> dict:
    """Safe automated resolution for the proposed system, read from ml/reports/results.json."""
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    name = data["proposed_system"]
    s = data["systems"][name]["summary"]
    n = s["n_cases"]
    sar = s["safe_automated_resolution_rate"]
    return {"system": name, "data_version": data["data_version"], "split": data["split"], "n_cases": n,
            "safe_automated_resolution_rate": sar["value"], "safe_automated_resolution_ci95": sar["ci95"],
            "safe_automated_resolutions": round(sar["value"] * n), "automation_attempted_share":
            s["automation_attempted_share"], "unsafe": s["unsafe"], "source": Path(results_path).as_posix()}


def _inp(value, unit: str, kind: str, source: str, note: str = "") -> dict:
    return {"value": value, "unit": unit, "kind": kind, "source": source, "note": note}


def build_inputs(profile: dict, categories: list[dict], ml: dict) -> dict:
    """`categories` is figures.by_reason_category: one row per reason category."""
    by_reason = {r["reason_category"]: r for r in categories}
    complaint, transactional = by_reason.get("Complaint"), by_reason.get("Transactional")
    days = profile["window_days"]
    return {
        "disputes_in_window": _inp(profile["complaints"], "complaints", "measured", "gold.complaint_facts",
                                   f"{profile['first_date']} to {profile['last_date']}"),
        "window_days": _inp(days, "days", "measured", "gold.complaint_facts"),
        "disputes_per_year": _inp(round(profile["complaints"] / days * 365, 1), "complaints/year", "measured",
                                  "disputes_in_window / window_days * 365"),
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
        "contacts_per_dispute_floor": _inp(1, "contacts", "assumption", "modeling choice",
                                           "one intake contact per complaint"),
        "contacts_per_dispute_upper": _inp(
            round(1 / complaint["fcr_rate"], 3) if complaint and complaint["fcr_rate"] else None, "contacts",
            "assumption",
            "1 / complaint_contact_fcr", "every unresolved contact retries with the same FCR (geometric)"),
        "safe_automated_resolution_rate": _inp(ml["safe_automated_resolution_rate"], "share", "cited",
                                               ml["source"], f"offline, {ml['n_cases']} held-out generated cases"),
        "automation_attempted_share": _inp(ml["automation_attempted_share"], "share", "cited", ml["source"]),
        "handed_off_case_savings": _inp(0, "minutes", "assumption", "conservative choice",
                                        "a structured handoff may shorten the human contact; not measured"),
        "back_office_minutes_per_dispute": _inp(None, "minutes", "not_defined", "not in the data",
                                                "investigation and chargeback effort are excluded"),
        "automation_cost_per_attempted_case": _inp(None, "USD", "not_defined", "eval not run with an LLM",
                                                   "reported below as a break-even value instead"),
        "hourly_cost_usd": _inp(list(ILLUSTRATIVE_HOURLY_COST_USD), "USD/hour", "assumption",
                                "illustrative placeholder", "not sourced; replace with the bank's loaded cost"),
    }


def _scenario(name: str, seconds: float, contacts: float, inputs: dict) -> dict:
    per_year = inputs["disputes_per_year"]["value"]
    sar = inputs["safe_automated_resolution_rate"]["value"]
    attempted = inputs["automation_attempted_share"]["value"]
    minutes = seconds * contacts / 60
    hours_year = per_year * minutes / 60
    saved = hours_year * sar
    money = []
    for rate in ILLUSTRATIVE_HOURLY_COST_USD:
        money.append({"hourly_cost_usd": rate,
                      "human_cost_per_dispute_usd": round(minutes / 60 * rate, 2),
                      "human_cost_per_year_usd": round(hours_year * rate),
                      "savings_per_year_usd": round(saved * rate),
                      "break_even_automation_cost_per_attempted_case_usd":
                          round(minutes / 60 * rate * sar / attempted, 2) if attempted else None})
    return {"scenario": name, "handling_seconds": seconds, "contacts_per_dispute": contacts,
            "human_minutes_per_dispute": round(minutes, 2), "human_hours_per_year": round(hours_year, 1),
            "hours_saved_per_year": round(saved, 1), "money": money}


def scenarios(inputs: dict) -> list[dict]:
    low_s = inputs["handling_seconds_transactional_contact"]["value"]
    mid_s = inputs["handling_seconds_complaint_contact"]["value"]
    upper = inputs["contacts_per_dispute_upper"]["value"]
    out = []
    if low_s:
        out.append(_scenario("low: transactional contact time, one contact", low_s, 1, inputs))
    if mid_s:
        out.append(_scenario("central: complaint contact time, one contact", mid_s, 1, inputs))
        if upper:
            out.append(_scenario("high: complaint contact time, repeat contacts at complaint FCR", mid_s, upper,
                                 inputs))
    return out


def cost_model(profile: dict, categories: list[dict], ml: dict) -> dict:
    inputs = build_inputs(profile, categories, ml)
    return {"scope": "human intake handling of unrecognized-charge complaints; projection from offline results",
            "formula": {
                "human_minutes_per_dispute": "handling_seconds * contacts_per_dispute / 60",
                "hours_saved_per_year": "disputes_per_year * human_minutes_per_dispute / 60 "
                                        "* safe_automated_resolution_rate",
                "break_even_automation_cost_per_attempted_case":
                    "human cost per dispute * safe_automated_resolution_rate / automation_attempted_share"},
            "inputs": inputs, "scenarios": scenarios(inputs)}
