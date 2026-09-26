"""data_engineering/slice.py on the synthetic fixture warehouse (gold built on a copy). No organizer data."""

from __future__ import annotations

import duckdb
import pytest

from agent.tools.repository import WarehouseRepository
from data_engineering.slice import CUSTOMER_TABLES, content_digest, slice_warehouse
from tests.agent.conftest import warehouse  # noqa: F401 (fixture)


def _q(db, sql, params=None):
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(sql, params or []).fetchall()


@pytest.fixture(scope="module")
def picked(warehouse):
    rows = _q(warehouse, "SELECT customer_id FROM silver.customers WHERE customer_id IN "
                         "(SELECT customer_id FROM silver.transactions) ORDER BY customer_id LIMIT 3")
    return {rows[0][0]: "normal", rows[1][0]: "declined", rows[2][0]: "human"}


@pytest.fixture(scope="module")
def sliced(warehouse, picked, tmp_path_factory):
    target = tmp_path_factory.mktemp("slice") / "slice.duckdb"
    counts = slice_warehouse(warehouse, target, picked, "fixture.duckdb")
    return target, counts


def test_rows_of_the_chosen_customers_are_copied_as_they_are(warehouse, picked, sliced):
    target, counts = sliced
    ids = sorted(picked)
    for schema, name in CUSTOMER_TABLES:
        sql = f"SELECT * FROM {schema}.{name} WHERE customer_id IN (SELECT unnest(?::VARCHAR[])) ORDER BY ALL"
        source_rows = _q(warehouse, sql, [ids])
        assert source_rows, f"{schema}.{name} has rows for the chosen customers"
        assert _q(target, f"SELECT * FROM {schema}.{name} ORDER BY ALL") == source_rows, "lineage columns untouched"
        assert counts[f"{schema}.{name}"] == len(source_rows)
    others = _q(target, "SELECT count(*) FROM silver.transactions WHERE customer_id NOT IN "
                        "(SELECT unnest(?::VARCHAR[]))", [ids])[0][0]
    assert others == 0


def test_the_slice_passes_the_repository_freshness_check_and_keeps_the_view(sliced, picked):
    target, _ = sliced
    repo = WarehouseRepository(target)  # raises WarehouseNotReady when gold is missing or older than silver
    repo.close()
    policy_rows = _q(target, "SELECT count(*), count(customer_country) FROM gold.dispute_policy_inputs")[0]
    assert policy_rows[0] > 0 and policy_rows[0] == policy_rows[1]


def test_control_tables_keep_lineage_and_record_the_scenarios(warehouse, sliced, picked):
    target, _ = sliced
    assert _q(target, "SELECT * FROM control.runs ORDER BY ALL") == _q(warehouse, "SELECT * FROM control.runs ORDER BY ALL")
    ledger = {r[0] for r in _q(target, "SELECT source_file FROM control.file_ledger")}
    used = {r[0] for r in _q(target, "SELECT _source_file FROM bronze.transactions UNION "
                                     "SELECT _source_file FROM bronze.customers UNION "
                                     "SELECT _source_file FROM bronze.products")}
    assert ledger == used
    assert dict(_q(target, "SELECT customer_id, scenario FROM control.demo_slice")) == picked
    assert {r[0] for r in _q(target, "SELECT source_warehouse FROM control.demo_slice")} == {"fixture.duckdb"}


def test_the_content_digest_is_stable_across_rebuilds(warehouse, picked, sliced, tmp_path):
    again = tmp_path / "again.duckdb"
    slice_warehouse(warehouse, again, picked, "fixture.duckdb")
    assert content_digest(again) == content_digest(sliced[0])


def test_unknown_customers_fail_and_leave_no_file(warehouse, tmp_path):
    target = tmp_path / "bad.duckdb"
    with pytest.raises(ValueError, match="not in the source"):
        slice_warehouse(warehouse, target, {"NOPE-1": "normal"}, "fixture.duckdb")
    assert not target.exists()
    with pytest.raises(ValueError, match="no customers"):
        slice_warehouse(warehouse, target, {}, "fixture.duckdb")
    with pytest.raises(FileNotFoundError):
        slice_warehouse(tmp_path / "missing.duckdb", target, {"X": "normal"}, "x")
