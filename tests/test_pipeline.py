"""Full pipeline run over the fixture, checked against the manifest (the injected-issue ground truth)."""

import json
from pathlib import Path

import pytest

from .conftest import scalar, silver_rows

TABLES = ["customers", "products", "branches", "service_agents", "marketing_campaigns", "transactions",
          "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events", "complaints",
          "campaign_sends", "daily_exchange_rates"]


@pytest.mark.parametrize("table", TABLES)
def test_silver_row_count_matches_manifest(full_run, fixture_source, table):
    report, _ = full_run
    expected = fixture_source[1]["tables"][table]
    rows = report["tables"][table]["rows"]
    assert rows["bronze_in"] == expected["rows_written"]
    assert rows["silver_total"] == expected["expected_silver_rows"]


@pytest.mark.parametrize("table", TABLES)
def test_primary_key_is_unique_in_silver(full_run, contracts, table):
    _, db = full_run
    pk = ", ".join(contracts[table].primary_key)
    dupes = scalar(db, f"SELECT count(*) FROM (SELECT {pk} FROM silver.{table} GROUP BY ALL HAVING count(*) > 1)")
    nulls = scalar(db, f"SELECT count(*) FROM silver.{table} WHERE {contracts[table].primary_key[0]} IS NULL")
    assert (dupes, nulls) == (0, 0)


@pytest.mark.parametrize("table", TABLES)
def test_duplicates_removed_match_manifest(full_run, fixture_source, table):
    report, _ = full_run
    assert report["tables"][table]["rows"]["exact_duplicates"] == fixture_source[1]["tables"][table]["exact_duplicates"]


@pytest.mark.parametrize("table", TABLES)
def test_quarantine_counts_by_reason_match_manifest(full_run, fixture_source, table):
    report, db = full_run
    expected = fixture_source[1]["tables"][table]["quarantine"]
    assert report["tables"][table]["quarantine_by_reason"] == expected
    stored = scalar(db, "SELECT count(*) FROM quarantine.records WHERE table_name = ?", [table])
    assert stored == sum(expected.values())


@pytest.mark.parametrize("table", TABLES)
def test_warnings_match_manifest(full_run, fixture_source, table):
    report, _ = full_run
    assert report["tables"][table]["warnings_by_reason"] == fixture_source[1]["tables"][table]["warnings"]


def test_every_reason_code_is_exercised(full_run):
    report, _ = full_run
    seen = {r for t in report["tables"].values() for r in t["quarantine_by_reason"]}
    assert seen == {"null_pk", "bad_enum", "out_of_range", "orphan_fk", "type_cast_error", "null_required"}


@pytest.mark.parametrize("table", TABLES)
def test_late_arrivals_match_manifest(full_run, fixture_source, table):
    report, _ = full_run
    expected = fixture_source[1]["tables"][table]
    section = report["tables"][table]
    assert section["rows"]["superseded_versions"] == expected["late_updates"]
    lag_rows = section["late_arrivals"]["event_lag_rows"]
    assert (lag_rows or 0) == expected["late_arrival_rows"]


def test_late_versions_win_in_silver(full_run, fixture_source):
    _, db = full_run
    manifest = fixture_source[1]["tables"]
    for tx_id, final in manifest["transactions"]["expected_final"].items():
        status = scalar(db, "SELECT transaction_status FROM silver.transactions WHERE transaction_id = ?", [tx_id])
        assert status == final["transaction_status"]
    for complaint_id, final in manifest["complaints"]["expected_final"].items():
        status = scalar(db, "SELECT status FROM silver.complaints WHERE complaint_id = ?", [complaint_id])
        assert status == final["status"]


def test_schema_drift_is_reported_not_fatal(full_run, fixture_source):
    report, db = full_run
    events = {(e["kind"], e["column"]): e for e in report["tables"]["transactions"]["drift_events"]}
    expected = fixture_source[1]["tables"]["transactions"]["drift"]
    assert events[("unexpected_column", "installments")]["files"] == expected["unexpected_column"]["installments"]
    assert events[("type_changed", "amount")]["variants"] == {"DOUBLE": 59, "UTF8": 1}
    assert all(not report["tables"][t]["drift_events"] for t in TABLES if t != "transactions")
    # the new column is kept raw in bronze and stays out of the contract-shaped silver table
    assert scalar(db, "SELECT count(installments) FROM bronze.transactions") > 0
    assert scalar(db, "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'silver' "
                      "AND table_name = 'transactions' AND column_name = 'installments'") == 0


def test_quarantine_keeps_raw_value_and_reason(full_run):
    _, db = full_run
    raw = scalar(db, "SELECT list(raw_record ->> 'amount' ORDER BY pk_value) FROM quarantine.records "
                     "WHERE list_contains(details, 'amount:type_cast_error')")
    assert len(raw) == 6
    assert all("," in value for value in raw)  # e.g. "1.234,56": a decimal comma the contract type cannot hold


def test_unique_violations_reported(full_run, fixture_source):
    report, _ = full_run
    expected = fixture_source[1]["tables"]["customers"]["unique_duplicate_values"]["document_number"]
    assert report["tables"]["customers"]["unique_violations"]["document_number"]["values"] == expected


def test_lineage_on_every_silver_row(full_run, fixture_source):
    _, db = full_run
    source = fixture_source[0]
    for table in TABLES:
        missing = scalar(db, f"SELECT count(*) FROM silver.{table} WHERE _source_file IS NULL OR _run_id IS NULL")
        assert missing == 0, table
    sample = scalar(db, "SELECT any_value(_source_file) FROM silver.transactions")
    assert (Path(source) / sample).exists()


def test_orphan_rates_and_unknown_enum_profile(full_run):
    report, _ = full_run
    tx = report["tables"]["transactions"]
    assert tx["orphan_rate"]["customer_id"]["orphans"] == 6
    assert tx["orphan_rate"]["product_id"]["orphans"] == 5
    profile = report["tables"]["products"]["profiles"]["product_type"]
    assert profile["distinct"] >= 1 and profile["top"]


def test_null_rates_near_dictionary_rate(full_run):
    report, _ = full_run
    rate = report["tables"]["customers"]["null_rate"]["occupation"]
    assert 0.02 < rate < 0.10
    assert report["tables"]["customers"]["null_rate"]["city"] == 0.0


def test_report_written_and_labeled(full_run):
    report, _ = full_run
    on_disk = json.loads(Path(report["report_path"]).read_text(encoding="utf-8"))
    assert on_disk["source_label"].startswith("SYNTHETIC TEST FIXTURE")
    assert on_disk["run_id"] == report["run_id"]
    assert set(on_disk["tables"]) == set(TABLES)
    assert on_disk["totals"]["silver_total"] == sum(t["rows"]["silver_total"] for t in on_disk["tables"].values())


def test_silver_types_follow_contract(full_run, contracts):
    _, db = full_run
    kind = scalar(db, "SELECT data_type FROM information_schema.columns WHERE table_schema = 'silver' "
                      "AND table_name = 'transactions' AND column_name = 'amount'")
    assert kind == "DECIMAL(15,2)"
    assert len(silver_rows(db, contracts["branches"])) == 30
