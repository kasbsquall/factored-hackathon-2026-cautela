"""The tools read the gold serving tables: same answers as the silver reads they replaced, and a clear refusal when
gold is missing or older than silver."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta

import duckdb
import pytest

from agent.tools.repository import TX_COLUMNS, WarehouseNotReady, WarehouseRepository
from tests.agent.conftest import NOW


def _copy(src, tmp_path):
    target = tmp_path / "copy.duckdb"
    shutil.copy2(src, target)
    return target


def test_refuses_a_warehouse_without_gold(full_run):
    with pytest.raises(WarehouseNotReady, match="gold serving tables missing"):
        WarehouseRepository(full_run[1])


def test_refuses_gold_older_than_silver(warehouse, tmp_path):
    db = _copy(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        con.execute("INSERT INTO control.runs (run_id, started_at, status) VALUES ('later', ?, 'succeeded')",
                    [datetime.now() + timedelta(days=1)])
    with pytest.raises(WarehouseNotReady, match="older than the latest silver run"):
        WarehouseRepository(db)


def test_transaction_reads_match_the_silver_reads_they_replaced(rig, people, query):
    cid = people["alice"]["customer_id"]
    start, end = NOW.replace(tzinfo=None) - timedelta(days=90), NOW.replace(tzinfo=None)
    silver = query(f"SELECT {', '.join(TX_COLUMNS)} FROM silver.transactions WHERE customer_id = ? AND "
                   "transaction_date BETWEEN ? AND ? ORDER BY transaction_date DESC, transaction_id LIMIT 50",
                   [cid, start, end])
    assert silver and rig.repo.transactions_of(cid, NOW - timedelta(days=90), NOW, 50) == silver
    tid = silver[0]["transaction_id"]
    old = query(f"SELECT t.{', t.'.join(TX_COLUMNS)}, p.product_type FROM silver.transactions t "
                "LEFT JOIN silver.products p USING (product_id) WHERE t.transaction_id = ?", [tid])[0]
    assert rig.repo.get_transaction(tid) == old
    facts = rig.repo.policy_inputs(tid)
    assert {k: facts[k] for k in ("customer_id", "amount", "amount_usd", "channel", "transaction_status",
                                  "fraud_score", "is_fraud", "product_type")} == \
           {k: old[k] for k in ("customer_id", "amount", "amount_usd", "channel", "transaction_status",
                                "fraud_score", "is_fraud", "product_type")}
    assert rig.repo.owner_of("transaction", tid) == cid


def test_profile_comes_from_gold_and_contact_from_the_identity_directory(rig, people, query):
    cid = people["alice"]["customer_id"]
    silver = query("SELECT first_name, last_name, country, segment, document_number FROM silver.customers "
                   "WHERE customer_id = ?", [cid])[0]
    profile = rig.repo.get_customer(cid)
    assert {k: profile[k] for k in ("first_name", "last_name", "country", "segment")} == \
           {k: silver[k] for k in ("first_name", "last_name", "country", "segment")}
    assert "document_number" not in profile and "email" not in profile
    assert rig.repo.contact_of(cid)["document_number"] == silver["document_number"]


def test_a_row_missing_from_gold_is_missing_for_the_tools(warehouse, people, query, tmp_path):
    tid = query("SELECT transaction_id FROM silver.transactions WHERE customer_id = ? LIMIT 1",
                [people["alice"]["customer_id"]])[0]["transaction_id"]
    db = _copy(warehouse, tmp_path)
    with duckdb.connect(str(db)) as con:
        con.execute("DELETE FROM gold.customer_transactions WHERE transaction_id = ?", [tid])
    repo = WarehouseRepository(db)
    try:
        assert repo.get_transaction(tid) is None
        assert repo.owner_of("transaction", tid) is None
        assert repo.policy_inputs(tid) is None
    finally:
        repo.close()
