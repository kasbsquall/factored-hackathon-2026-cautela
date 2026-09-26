"""Workflow evidence on the fixture warehouse: every figure is checked against an independent query on silver,
the cost model arithmetic is checked by hand, and the outputs carry their denominators."""

import json

import pytest

from data_analytics import run as analytics
from data_analytics.cost_model import ILLUSTRATIVE_HOURLY_COST_USD, ml_rates
from data_analytics.figures import wilson

from .conftest import query

DISPUTE = "(category = 'Cargo no reconocido' OR subcategory = 'Cargo no reconocido')"


@pytest.fixture()
def evidence(gold_db, ml_results):
    _, db = gold_db
    return analytics.collect(db, ml_results), db


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


def test_cost_model_arithmetic_and_labels(evidence):
    ev, _ = evidence
    cm = ev["cost_model"]
    kinds = {v["kind"] for v in cm["inputs"].values()}
    assert kinds <= {"measured", "cited", "assumption", "not_defined"}
    assert all(v["value"] is None for v in cm["inputs"].values() if v["kind"] == "not_defined")
    assert cm["inputs"]["hourly_cost_usd"]["kind"] == "assumption"
    per_year = cm["inputs"]["disputes_per_year"]["value"]
    for s in cm["scenarios"]:
        minutes = s["handling_seconds"] * s["contacts_per_dispute"] / 60
        assert s["human_minutes_per_dispute"] == round(minutes, 2)
        assert s["hours_saved_per_year"] == round(per_year * minutes / 60 * 0.5, 1)
        for m, rate in zip(s["money"], ILLUSTRATIVE_HOURLY_COST_USD, strict=True):
            assert m["break_even_automation_cost_per_attempted_case_usd"] == round(minutes / 60 * rate * 0.5 / 0.625, 2)


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
    charts = [p for p in written if p.suffix == ".json"]
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
