"""gold.customer_products: the serving table for product reads and the product ownership check. It must return what
the tool repository reads from silver today, patch an owner change incrementally, report stale gold until it runs,
and refuse a table whose owner disagrees with silver."""

import duckdb
import pytest

from data_engineering.freshness.status import gold_freshness
from data_engineering.gold.checks import run_checks
from data_engineering.gold.run import run_gold

from .conftest import query

# The product reads of agent/tools/repository.py, on silver today and on gold after the switch.
PRODUCTS_OF = ("SELECT product_id, product_type, product_number, currency, product_status FROM {t} "
               "WHERE customer_id = ? ORDER BY product_id")
GET_PRODUCT = "SELECT product_id, customer_id, product_type, product_status FROM {t} WHERE product_id = ?"
OWNER_OF = "SELECT customer_id FROM {t} WHERE product_id = ?"


def test_gold_answers_every_repository_product_read_like_silver(gold_db):
    _, db = gold_db
    assert query(db, "SELECT count(*) FROM gold.customer_products") == query(db, "SELECT count(*) FROM silver.products")
    customers = [r[0] for r in query(db, "SELECT DISTINCT customer_id FROM silver.products ORDER BY 1 LIMIT 50")]
    for cid in customers:
        assert query(db, PRODUCTS_OF.format(t="gold.customer_products"), [cid]) == \
            query(db, PRODUCTS_OF.format(t="silver.products"), [cid])
    for (pid,) in query(db, "SELECT product_id FROM silver.products ORDER BY 1 LIMIT 50"):
        for sql in (GET_PRODUCT, OWNER_OF):
            assert query(db, sql.format(t="gold.customer_products"), [pid]) == \
                query(db, sql.format(t="silver.products"), [pid])
    assert query(db, OWNER_OF.format(t="gold.customer_products"), ["NO-SUCH-PRODUCT"]) == []


def _move_one_product(db) -> tuple[str, str]:
    """Give one product to another customer, as a new load would: a new bronze version and the silver upsert."""
    with duckdb.connect(str(db)) as con:
        pid, old = con.execute("SELECT product_id, customer_id FROM silver.products ORDER BY product_id LIMIT 1"
                               ).fetchone()
        new = con.execute("SELECT customer_id FROM silver.customers WHERE customer_id <> ? ORDER BY 1 LIMIT 1",
                          [old]).fetchone()[0]
        con.execute("UPDATE silver.products SET customer_id = ?, _ingested_at = "
                    "(SELECT max(_ingested_at) + INTERVAL 1 SECOND FROM silver.products) WHERE product_id = ?",
                    [new, pid])
        con.execute("INSERT INTO bronze.products SELECT * REPLACE (? AS customer_id) FROM bronze.products "
                    "WHERE product_id = ?", [new, pid])
    return pid, new


def test_an_owner_change_is_stale_until_gold_runs_and_then_patched(gold_copy, tmp_path):
    pid, new = _move_one_product(gold_copy)
    with duckdb.connect(str(gold_copy), read_only=True) as con:
        assert gold_freshness(con)["tables"]["customer_products"] == "stale"
    report = run_gold(gold_copy, reports_dir=tmp_path)
    section = report["tables"]["customer_products"]
    assert section["mode"] == "incremental"
    assert not [c for c in section["checks"] if c["violations"]]
    assert query(gold_copy, OWNER_OF.format(t="gold.customer_products"), [pid]) == [(new,)]
    with duckdb.connect(str(gold_copy), read_only=True) as con:
        assert gold_freshness(con)["tables"]["customer_products"] == "current"


def test_an_owner_that_disagrees_with_silver_fails_the_blocking_check(gold_copy, gold_contracts):
    _move_one_product(gold_copy)  # silver moved, gold not rebuilt: the served owner is now wrong
    with duckdb.connect(str(gold_copy), read_only=True) as con:
        results = {c["check"]: c["violations"] for c in run_checks(con, gold_contracts["customer_products"])}
    assert results["reconciliation:served_fields_match_silver"] == 1
    assert results["reconciliation:one_row_per_silver_product"] == 0


@pytest.mark.parametrize("column", ["currency", "product_status"])
def test_value_lists_match_the_silver_contract(gold_contracts, column):
    from data_engineering.contracts.loader import load_contracts
    silver = load_contracts()["products"].column(column)
    gold = next(c for c in gold_contracts["customer_products"].columns if c.name == column)
    assert gold.allowed_values == silver.accepted_values
