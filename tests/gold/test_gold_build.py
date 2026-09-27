"""Gold build on the fixture warehouse: contracts hold, lineage is complete, reruns are no-ops, and a failing
check rolls the table back."""

import duckdb
import pytest

from data_engineering.gold.build import _unsafe_for_increment, build_table
from data_engineering.gold.checks import GoldQualityError
from data_engineering.gold.contract import GoldColumn
from data_engineering.gold.run import run_gold

from .conftest import gold_rows, query

ROW_TABLES = {"customer_profile": "customers", "customer_transactions": "transactions",
              "complaint_facts": "complaints"}


def test_every_table_builds_and_passes_every_check(gold_db, gold_contracts):
    report, _ = gold_db
    assert set(report["tables"]) == set(gold_contracts)
    for name, section in report["tables"].items():
        failed = [c for c in section["checks"] if c["violations"]]
        assert failed == [], name
        assert section["mode"] == ("view" if gold_contracts[name].kind == "view" else "full")


@pytest.mark.parametrize("table,silver", sorted(ROW_TABLES.items()))
def test_row_tables_have_one_row_per_silver_key(gold_db, table, silver):
    _, db = gold_db
    assert query(db, f"SELECT count(*) FROM gold.{table}") == query(db, f"SELECT count(*) FROM silver.{silver}")


@pytest.mark.parametrize("table,silver", sorted(ROW_TABLES.items()))
def test_row_lineage_points_at_the_silver_row(gold_db, gold_contracts, table, silver):
    report, db = gold_db
    key = gold_contracts[table].primary_key[0]
    bad = query(db, f"""
        SELECT count(*) FROM gold.{table} g LEFT JOIN silver.{silver} s ON s.{key} = g._source_key
        WHERE s.{key} IS NULL OR g._source_key <> g.{key} OR g._source_table <> 'silver.{silver}'
           OR NOT list_contains(g._source_run_ids, s._run_id) OR g._gold_run_id <> ?""", [report["run_id"]])
    assert bad == [(0,)]


def test_source_run_ids_are_real_silver_runs(gold_db, gold_contracts):
    _, db = gold_db
    runs = {r[0] for r in query(db, "SELECT run_id FROM control.runs WHERE status = 'succeeded'")}
    for name in gold_contracts:
        ids = {r[0] for r in query(db, f"SELECT DISTINCT unnest(_source_run_ids) FROM gold.{name}")}
        assert ids and ids <= runs, name


def test_rerun_on_unchanged_silver_changes_nothing(gold_copy, gold_contracts, tmp_path):
    before = {n: gold_rows(gold_copy, n, c, drop=()) for n, c in gold_contracts.items()}
    report = run_gold(gold_copy, reports_dir=tmp_path)
    for name, section in report["tables"].items():
        assert section["mode"] in ("skipped", "view"), name
        assert gold_rows(gold_copy, name, gold_contracts[name], drop=()) == before[name], name


def test_policy_view_carries_customer_country_and_product_state(gold_db):
    _, db = gold_db
    mismatches = query(db, """
        SELECT count(*) FROM gold.dispute_policy_inputs v JOIN silver.transactions t USING (transaction_id)
        LEFT JOIN silver.customers c ON c.customer_id = t.customer_id
        LEFT JOIN silver.products p ON p.product_id = t.product_id
        WHERE v.customer_country IS DISTINCT FROM c.country OR v.product_status IS DISTINCT FROM p.product_status
           OR v.amount <> t.amount""")
    assert mismatches == [(0,)]


def test_repeat_features_match_an_independent_computation(gold_db):
    _, db = gold_db
    mismatches = query(db, """
        WITH x AS (
            SELECT c.complaint_id,
                   EXISTS (SELECT 1 FROM silver.complaints p WHERE p.customer_id = c.customer_id
                           AND p.complaint_id <> c.complaint_id AND p.creation_date <= c.creation_date
                           AND p.creation_date >= c.creation_date - INTERVAL 90 DAY
                           AND (p.creation_date, p.complaint_id) < (c.creation_date, c.complaint_id)) AS prior90,
                   EXISTS (SELECT 1 FROM silver.complaints q WHERE q.customer_id = c.customer_id
                           AND q.complaint_id <> c.complaint_id AND q.creation_date <= c.creation_date + INTERVAL 30 DAY
                           AND (q.creation_date, q.complaint_id) > (c.creation_date, c.complaint_id)) AS next30
            FROM silver.complaints c)
        SELECT count(*) FROM x JOIN gold.complaint_facts g USING (complaint_id)
        WHERE g.prior_complaint_90d <> x.prior90 OR g.next_complaint_30d <> x.next30""")
    assert mismatches == [(0,)]


def test_unrecognized_charge_complaints_are_flagged(gold_db):
    _, db = gold_db
    flagged = query(db, "SELECT count(*) FROM gold.complaint_facts WHERE is_unrecognized_charge")[0][0]
    assert flagged == query(db, "SELECT count(*) FROM silver.complaints WHERE category = 'Cargo no reconocido' "
                                "OR subcategory = 'Cargo no reconocido'")[0][0]
    assert flagged > 0


def test_a_failing_check_rolls_the_table_back(gold_copy, gold_contracts):
    contract = gold_contracts["complaint_outcomes"]
    strict = contract.model_copy(update={
        "description": "forces a rebuild", "columns": [
            c.model_copy(update={"max": 0.0}) if c.name == "sla_breach_rate" else c for c in contract.columns]})
    before = gold_rows(gold_copy, "complaint_outcomes", contract, drop=())
    with duckdb.connect(str(gold_copy)) as con:
        with pytest.raises(GoldQualityError, match="max"):
            build_table(con, strict, "run-that-fails")
        state = con.execute("SELECT last_run_id FROM control.gold_state WHERE gold_table = 'complaint_outcomes'"
                            ).fetchone()[0]
    assert gold_rows(gold_copy, "complaint_outcomes", contract, drop=()) == before
    assert state != "run-that-fails"


def test_sql_that_disagrees_with_the_contract_is_refused(gold_copy, gold_contracts):
    contract = gold_contracts["customer_profile"]
    wrong = contract.model_copy(update={"columns": [
        GoldColumn(name="products_total", classification="none", type="DOUBLE", nullable=False, description="wrong type")
        if c.name == "products_total" else c for c in contract.columns]})
    with duckdb.connect(str(gold_copy)) as con, pytest.raises(GoldQualityError, match="products_total"):
        build_table(con, wrong, "run-with-wrong-schema")


def _mark(lo: str, hi: str, rows: int) -> dict:
    return {"min_ingested_at": f"2026-01-01T{lo}", "max_ingested_at": f"2026-01-01T{hi}", "rows": rows}


@pytest.mark.parametrize("new,unsafe", [
    (_mark("10:00:00", "11:00:00", 120), False),  # new rows landed after the old watermark
    (_mark("10:00:00", "10:30:00", 120), True),   # watermark did not advance: new rows would be invisible
    (_mark("11:00:00", "11:00:00", 100), True),   # every row reloaded (silver full refresh can drop keys)
    (_mark("10:00:00", "11:00:00", 90), True),    # rows disappeared
])
def test_increment_fails_closed_on_unsafe_marks(new, unsafe):
    assert _unsafe_for_increment(_mark("10:00:00", "10:30:00", 100), new) is unsafe


def test_a_changed_definition_forces_a_full_rebuild(gold_copy, gold_contracts):
    contract = gold_contracts["complaint_facts"]
    changed = contract.model_copy(update={"description": "new definition"})
    with duckdb.connect(str(gold_copy)) as con:
        section = build_table(con, changed, "run-new-definition")
        dependent = build_table(con, gold_contracts["complaint_outcomes"], "run-after-input-changed")
        unchanged = build_table(con, gold_contracts["interaction_outcomes"], "run-after-input-changed")
    assert section["mode"] == "full"
    assert section["rows_inserted"] == section["rows_total"] > 0
    assert dependent["mode"] == "full"  # reads gold.complaint_facts, which was just rebuilt
    assert unchanged["mode"] == "skipped"
