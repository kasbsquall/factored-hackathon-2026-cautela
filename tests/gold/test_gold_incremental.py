"""Gold update correctness: silver loaded in two waves (the labeled fixture, as in tests/test_incremental.py) with
gold built after each wave must end identical to gold built once on a single full load. Only the run-specific
lineage (gold run id and silver run ids) may differ."""

import duckdb
import pytest

from data_engineering.gold.build import build_table
from data_engineering.gold.run import run_gold
from data_engineering.pipelines.run import run_pipeline

from ..test_incremental import _stage
from .conftest import gold_rows


@pytest.fixture(scope="module")
def gold_two_waves(tmp_path_factory, fixture_source):
    source, _ = fixture_source
    work = tmp_path_factory.mktemp("gold_incremental")
    staged, db, reports = work / "landing", work / "warehouse.duckdb", work / "reports"
    _stage(source, staged, wave=1)
    run_pipeline(str(staged), db, reports_dir=reports)
    first = run_gold(db, reports_dir=reports)
    _stage(source, staged, wave=2)
    run_pipeline(str(staged), db, reports_dir=reports)
    second = run_gold(db, reports_dir=reports)
    return first, second, db


def test_incremental_gold_converges_to_full_gold(gold_two_waves, gold_db, gold_contracts):
    _, _, inc_db = gold_two_waves
    _, full_db = gold_db
    for name, contract in gold_contracts.items():
        assert gold_rows(inc_db, name, contract) == gold_rows(full_db, name, contract), name


def test_second_wave_updates_only_what_changed(gold_two_waves, gold_contracts):
    """Snapshot tables (customers, products, branches) arrive whole in wave 1, so a table fed only by them is
    skipped; a table with a daily source that received new files is updated."""
    first, second, _ = gold_two_waves
    for name, contract in gold_contracts.items():
        if contract.kind == "view":
            continue
        sec = second["tables"][name]
        moved = sec["source_marks"] != first["tables"][name]["source_marks"]
        assert first["tables"][name]["mode"] == "full"
        if not moved:
            assert sec["mode"] == "skipped", name
        elif contract.kind == "row":
            assert sec["mode"] == "incremental", name
            assert 0 < sec["keys_changed"] <= sec["rows_total"], name
        else:
            assert sec["mode"] == "full", name
    assert second["tables"]["customer_profile"]["mode"] == "skipped"
    assert second["tables"]["customer_transactions"]["mode"] == "incremental"
    assert second["tables"]["complaint_facts"]["mode"] == "incremental"


def test_product_moving_to_another_customer_refreshes_both_owners(gold_copy, gold_contracts):
    """Silver keeps only the latest version of a product, so the previous owner is found through bronze."""
    with duckdb.connect(str(gold_copy)) as con:
        pid, old = con.execute("SELECT product_id, customer_id FROM silver.products ORDER BY product_id LIMIT 1"
                               ).fetchone()
        new = con.execute("SELECT customer_id FROM silver.customers WHERE customer_id <> ? ORDER BY 1 LIMIT 1",
                          [old]).fetchone()[0]
        later = "(SELECT max(_ingested_at) + INTERVAL 1 SECOND FROM silver.products)"
        con.execute(f"INSERT INTO bronze.products BY NAME SELECT * REPLACE (? AS customer_id, {later} AS _ingested_at) "
                    "FROM bronze.products WHERE product_id = ? LIMIT 1", [new, pid])
        con.execute(f"UPDATE silver.products SET customer_id = ?, _ingested_at = {later} WHERE product_id = ?",
                    [new, pid])
        section = build_table(con, gold_contracts["customer_profile"], "run-owner-change")
        counts = dict(con.execute("SELECT customer_id, products_total FROM gold.customer_profile "
                                  "WHERE customer_id IN (?, ?)", [old, new]).fetchall())
        expected = dict(con.execute("SELECT c.customer_id, count(p.product_id) FROM silver.customers c "
                                    "LEFT JOIN silver.products p USING (customer_id) WHERE c.customer_id IN (?, ?) "
                                    "GROUP BY 1", [old, new]).fetchall())
    assert section["mode"] == "incremental"
    assert section["keys_changed"] == 2
    assert counts == expected


def test_incremental_touches_fewer_rows_than_a_rebuild(gold_two_waves):
    _, second, _ = gold_two_waves
    tx = second["tables"]["customer_transactions"]
    assert tx["rows_inserted"] < tx["rows_total"]
