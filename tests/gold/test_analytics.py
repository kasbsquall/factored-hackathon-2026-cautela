"""Workflow evidence on the fixture warehouse: every figure is checked against an independent query on silver,
the cost model arithmetic is checked by hand, and the outputs carry their denominators."""

import json

import pytest

from agent.policy.engine import load_rules
from data_analytics import run as analytics
from data_analytics.cost_model import DEPLOYED_EVAL_CONFIG, ILLUSTRATIVE_HOURLY_COST_USD, eval_rates, ml_rates
from data_analytics.figures import wilson

from .conftest import query

DISPUTE = "(category = 'Cargo no reconocido' OR subcategory = 'Cargo no reconocido')"
EVAL_USD, EVAL_CONVERSATIONS, EVAL_SAFE = 0.08, 400, 120


@pytest.fixture()
def eval_results(tmp_path):
    """A minimal eval/results.json with the shape eval/run.py writes; values are test inputs, not results."""
    config = {"run_id": "llm-test", "description": "test", "summary": {
        "conversations": EVAL_CONVERSATIONS,
        "safe_automated_resolution": {"k": EVAL_SAFE, "n": 300, "rate": 0.4, "ci95": [0.35, 0.46]},
        "automation_attempted": {"k": 210, "n": 300, "rate": 0.7}},
        "cost": {"llm_calls": 1000, "llm_cost_usd_total": EVAL_USD}}
    path = tmp_path / "eval_results.json"
    path.write_text(json.dumps({"configs": {DEPLOYED_EVAL_CONFIG: config}}), encoding="utf-8")
    return path


@pytest.fixture()
def evidence(gold_db, ml_results, eval_results):
    _, db = gold_db
    return analytics.collect(db, ml_results, eval_results), db


def test_dispute_volume_and_rates_match_silver(evidence):
    ev, db = evidence
    n, breached, escalated = query(db, f"SELECT count(*), count(*) FILTER (WHERE sla_breached), count(*) FILTER "
                                       f"(WHERE status = 'Escalated') FROM silver.complaints WHERE {DISPUTE}")[0]
    d = ev["dispute_profile"]
    assert (d["complaints"], d["sla_breached"], d["escalated"]) == (n, breached, escalated)
    assert sum(r["complaints"] for r in d["channel_mix"]) == n
    assert sum(r["complaints"] for r in d["status_mix"]) == n


def test_resolution_percentiles_match_silver(evidence):
    ev, db = evidence
    p50, p90, k = query(db, f"SELECT quantile_cont(resolution_days, 0.5), quantile_cont(resolution_days, 0.9), "
                            f"count(resolution_days) FROM silver.complaints WHERE {DISPUTE}")[0]
    d = ev["dispute_profile"]
    assert (d["resolution_days_p50"], d["resolution_days_p90"], d["with_resolution_days"]) == (p50, p90, k)


def test_workflow_shares_partition_all_complaints(evidence):
    ev, db = evidence
    w = ev["workflows"]
    total = query(db, "SELECT count(*) FROM silver.complaints")[0][0]
    assert sum(r["complaints"] for r in w) == total
    assert [r["volume_rank"] for r in w] == sorted(r["volume_rank"] for r in w)
    assert all(r["share_ci95"][0] <= r["share_of_complaints"] <= r["share_ci95"][1] for r in w)


def test_fcr_by_reason_category_matches_silver(evidence):
    ev, db = evidence
    expected = {r[0]: (r[1], r[2]) for r in query(db, "SELECT reason_category, count(*) FILTER (WHERE was_resolved), "
                                                      "count(was_resolved) FROM silver.call_center_interactions "
                                                      "GROUP BY 1")}
    got = {r["reason_category"]: (r["resolved_first_contact"], r["fcr_denominator"])
           for r in ev["fcr_by_reason_category"]}
    assert got == expected
    rates = [r["fcr_rate"] for r in ev["fcr_by_reason_category"]]
    assert rates == sorted(rates)  # the first row is the lowest FCR the report quotes


def test_demand_series_add_up_to_the_disputes(evidence):
    ev, _ = evidence
    n, dem = ev["dispute_profile"]["complaints"], ev["demand"]
    for key in ("disputes_by_hour", "disputes_by_weekday", "disputes_by_country"):
        assert sum(r["contacts"] for r in dem[key]) == n, key
    daily = dem["disputes_daily"][0]
    assert daily["contacts"] == n
    assert daily["days"] == ev["dispute_profile"]["window_days"]
    assert sum(r["contacts"] for r in dem["disputes_daily_by_country"]) == n
    assert daily["p50_per_day"] <= daily["p95_per_day"] <= daily["max_per_day"]


def test_breakdowns_add_up_and_use_the_customer_base(evidence):
    ev, db = evidence
    n = ev["dispute_profile"]["complaints"]
    customers = dict(query(db, "SELECT country, count(*) FROM silver.customers GROUP BY 1"))
    for dim in ("country", "segment"):
        assert sum(r["complaints"] for r in ev["breakdowns"][dim]) == n
    for r in ev["breakdowns"]["country"]:
        assert r["customers"] == customers.get(r["value"])
        if r["customers"]:
            assert r["disputes_per_1000_customers"] == round(1000 * r["complaints"] / r["customers"], 2)


def test_ml_rates_read_the_proposed_system(ml_results):
    ml = ml_rates(ml_results)
    assert (ml["safe_automated_resolution_rate"], ml["n_cases"], ml["safe_automated_resolutions"]) == (0.5, 200, 100)
    assert ml["automation_attempted_share"] == 0.625


def test_eval_rates_read_the_deployed_configuration_and_its_spend(eval_results):
    ev = eval_rates(eval_results)
    assert (ev["config"], ev["safe_automated_resolution_rate"], ev["safe_automated_resolutions"]) == \
        (DEPLOYED_EVAL_CONFIG, 0.4, EVAL_SAFE)
    assert ev["llm_usd_per_conversation"] == round(EVAL_USD / EVAL_CONVERSATIONS, 7)
    assert ev["llm_usd_per_safe_automated_resolution"] == round(EVAL_USD / EVAL_SAFE, 7)


def test_cost_model_arithmetic_and_labels(evidence):
    ev, _ = evidence
    cm = ev["cost_model"]
    inputs = cm["inputs"]
    kinds = {v["kind"] for v in inputs.values()}
    assert kinds <= {"measured", "cited", "assumption", "not_defined"}
    assert all(v["value"] is None for v in inputs.values() if v["kind"] == "not_defined")
    assert inputs["hourly_cost_usd"]["kind"] == "assumption"
    assert inputs["call_center_routed_to_flow"]["kind"] == "assumption"
    assert inputs["safe_automated_resolution_conservative"]["value"] == 0.4  # eval/results.json, deployed config
    assert inputs["safe_automated_resolution_optimistic"]["value"] == 0.5  # ml/reports/results.json
    bot = EVAL_USD / EVAL_CONVERSATIONS
    assert inputs["llm_usd_per_conversation"]["value"] == round(bot, 7)
    chat = inputs["chat_eligible_share"]["value"]
    wide = round(chat + inputs["call_center_share"]["value"], 4)
    assert {(p["reach_share"], p["rate"]) for p in cm["projections"]} == {
        (r, k) for r in (chat, wide) for k in ("conservative", "optimistic")}
    per_year = inputs["disputes_per_year"]["value"]
    for p in cm["projections"]:
        minutes, sar = p["human_minutes_per_dispute"], p["safe_automated_resolution_rate"]
        routed = per_year * p["reach_share"]
        assert p["hours_saved_per_year"] == pytest.approx(routed * sar * minutes / 60, abs=0.2)
        assert p["hours_saved_per_year"] + p["human_hours_left_per_year"] == \
            pytest.approx(p["human_hours_routed_per_year"], abs=0.2)
        for m, rate in zip(p["money"], ILLUSTRATIVE_HOURLY_COST_USD, strict=True):
            human = minutes / 60 * rate
            # a failed attempt costs its tokens plus the full human handling time, never zero
            assert m["cost_per_routed_dispute_with_bot_usd"] == pytest.approx(bot + (1 - sar) * human, abs=0.01)
            assert m["net_savings_per_year_usd"] == pytest.approx(routed * (sar * human - bot), abs=5)
            assert m["break_even_bot_usd_per_conversation"] == pytest.approx(sar * human, abs=0.01)


def test_channel_reach_matches_the_reception_channels(evidence):
    ev, db = evidence
    counts = dict(query(db, f"SELECT reception_channel, count(*) FROM silver.complaints WHERE {DISPUTE} GROUP BY 1"))
    r = ev["channel_reach"]
    assert r["chat_eligible"] == counts.get("App", 0) + counts.get("Web", 0)
    assert r["call_center"] == counts.get("Call Center", 0)
    assert sum(x["complaints"] for x in r["digital_event_30d_before_complaint"]) == r["complaints"]


def test_workflow_ranking_is_hours_times_unresolved_share(evidence):
    ev, db = evidence
    rk = ev["workflow_ranking"]
    rows = rk["rows"]
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    hours = [r["unresolved_contact_hours_per_year"] for r in rows]
    assert hours == sorted(hours, reverse=True)
    days = rk["contact_center"]["window_days"]
    per_reason = dict(query(db, "SELECT reason_category, count(*) FROM silver.call_center_interactions GROUP BY 1"))
    for r in rows:
        assert r["unresolved_contact_hours_per_year"] == pytest.approx(
            r["contacts_per_year"] * r["handling_seconds"] / 3600 * (1 - r["first_contact_resolution"]), abs=0.2)
        if r["verifiable"] != "yes":
            assert r["contacts_per_year"] == pytest.approx(
                per_reason[r["proxy"].removeprefix("contact reason ")] / days * 365, abs=0.1)
    assert sum(r["verifiable"] == "yes" for r in rows) == 1


def test_thresholds_are_read_from_the_policy_and_add_up(evidence):
    ev, db = evidence
    rules = load_rules()
    th = ev["thresholds"]
    a, d = th["amount"], th["fraud"]["disputable"]
    assert a["threshold_usd"] == float(rules["amount_review"]["threshold_usd"])
    assert th["fraud"]["score_at_least"] == float(rules["fraud_escalation"]["fraud_score_at_least"])
    assert sum(r["charges"] for r in a["by_type"]) == a["overall"]["charges"] == d["charges"]
    assert sum(r["at_or_above"] for r in a["by_type"]) == a["overall"]["at_or_above"]
    # independent count on silver for charges already in USD (no conversion involved)
    k = query(db, "SELECT count(*) FROM silver.transactions WHERE transaction_type <> 'Deposit' "
                  "AND transaction_status IN ('Approved', 'Pending') AND coalesce(amount_usd, "
                  "CASE WHEN currency = 'USD' THEN amount END) >= ?", [a["threshold_usd"]])[0][0]
    assert a["overall"]["at_or_above"] >= k
    assert d["routed_by_score_only"] <= d["routed"] <= d["charges"]
    assert th["either"]["routed"] >= max(a["overall"]["at_or_above"], d["routed"])


def test_survey_csat_by_first_contact_resolution_matches_silver(evidence):
    ev, db = evidence
    expected = dict(query(db, """
        SELECT i.was_resolved, count(*) FROM silver.satisfaction_surveys s
        JOIN silver.call_center_interactions i USING (interaction_id) WHERE s.survey_type = 'CSAT' GROUP BY 1"""))
    got = {r["was_resolved"]: r["surveys"] for r in ev["satisfaction"]["surveys"]["by_first_contact_resolution"]}
    assert got == expected


def test_handoff_queue_scales_daily_demand_by_reach_and_failure(evidence):
    ev, _ = evidence
    daily = ev["demand"]["disputes_daily"][0]
    scenarios = ev["handoff_queue"]["scenarios"]
    assert len(scenarios) == 4
    for s in scenarios:
        assert s["handoffs_per_day_mean"] == pytest.approx(daily["mean_per_day"] * s["handoff_share_of_disputes"],
                                                           abs=0.01)
        assert s["handoffs_peak_hour"] >= s["handoffs_median_hour"]


def test_data_limits_report_templating_and_links(evidence):
    ev, db = evidence
    lim = ev["data_limits"]
    assert lim["complaint_links"]["complaints"] == query(db, "SELECT count(*) FROM silver.complaints")[0][0]
    assert lim["transcripts"]["distinct_full_text"] <= lim["transcripts"]["transcripts"]
    rf = lim["repeat_flag"]
    assert rf["flagged_and_confirmed"] <= min(rf["flagged"], rf["with_prior_complaint_90d"])


def test_outputs_state_unit_and_denominator(evidence, tmp_path):
    ev, _ = evidence
    written = analytics.write_outputs(ev, tmp_path)
    charts = [p for p in written if p.suffix == ".json" and p.name != "insights.json"]
    assert len(charts) == 10
    for path in charts:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["unit"] and payload["denominator"] and payload["data"] is not None, path.name
    report = (tmp_path / "why-this-workflow.md").read_text(encoding="utf-8")
    assert report.startswith("# Why unrecognized-charge disputes")
    assert "SYNTHETIC TEST FIXTURE" in report
    d = ev["dispute_profile"]
    assert f"({d['sla_breached']:,} / {d['complaints']:,})" in report
    assert "—" not in report
    assert "not defined yet" not in report and "has not been run with an LLM" not in report
    ins = json.loads((tmp_path / "insights.json").read_text(encoding="utf-8"))
    for key in ("workflow_ranking", "cost_per_resolution", "projection_range", "handoff_queue", "thresholds",
                "satisfaction", "provenance"):
        assert ins[key], key
    assert ins["cost_per_resolution"]["llm_usd_per_safe_automated_resolution"] == round(EVAL_USD / EVAL_SAFE, 7)
    assert ins["provenance"]["eval_run_id"] == "llm-test"


def test_cli_refuses_to_write_fixture_numbers_into_the_committed_reports(gold_db, ml_results, tmp_path,
                                                                         monkeypatch):
    _, db = gold_db
    committed = tmp_path / "reports"
    monkeypatch.setattr(analytics, "DEFAULT_OUT", committed.resolve())
    code = analytics.main(["--warehouse", str(db), "--ml-results", str(ml_results), "--out", str(committed)])
    assert code == 1
    assert not committed.exists()


def test_wilson_interval_brackets_the_estimate():
    lo, hi = wilson(30, 100)
    assert 0 <= lo < 0.3 < hi <= 1
    assert wilson(0, 0) is None
