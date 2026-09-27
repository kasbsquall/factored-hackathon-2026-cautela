"""Gold contracts: they load, they validate, and the build order respects gold-on-gold dependencies."""

import pytest
from pydantic import ValidationError

from data_engineering.gold.contract import GoldContract, build_order

SERVING = {"customer_profile", "customer_transactions", "dispute_policy_inputs"}
ANALYTICS = {"complaint_facts", "complaint_outcomes", "interaction_outcomes", "demand_by_hour", "demand_by_day",
             "workflow_selection"}


def test_every_gold_table_has_a_contract_and_sql(gold_contracts):
    assert set(gold_contracts) == SERVING | ANALYTICS
    for contract in gold_contracts.values():
        assert contract.sql.strip()


def test_purpose_matches_the_intended_consumer(gold_contracts):
    assert {n for n, c in gold_contracts.items() if c.purpose == "serving"} == SERVING
    assert {n for n, c in gold_contracts.items() if c.purpose == "analytics"} == ANALYTICS


def test_build_order_puts_gold_inputs_first(gold_contracts):
    order = build_order(gold_contracts)
    for name, contract in gold_contracts.items():
        for source in contract.sources:
            if source.startswith("gold."):
                assert order.index(source.removeprefix("gold.")) < order.index(name), (name, source)


def test_row_tables_declare_changed_keys_for_every_source(gold_contracts):
    for contract in gold_contracts.values():
        if contract.kind == "row":
            assert set(contract.changed_keys) == set(contract.sources)


def _base(**overrides) -> dict:
    spec = {"table": "t", "kind": "row", "purpose": "serving", "description": "d", "primary_key": ["id"],
            "sources": ["customers"], "lineage_source": "customers",
            "changed_keys": {"customers": "SELECT customer_id FROM silver.customers WHERE _ingested_at > {wm}"},
            "columns": [{"name": "id", "classification": "none", "type": "VARCHAR", "nullable": False, "description": "key"}]}
    spec.update(overrides)
    return spec


def test_valid_minimal_contract():
    assert GoldContract.model_validate(_base()).table == "t"


@pytest.mark.parametrize("overrides", [
    {"changed_keys": {}},  # row table without incremental key queries
    {"changed_keys": {"customers": "SELECT customer_id FROM silver.customers"}},  # no watermark filter
    {"columns": [{"name": "id", "classification": "none", "type": "VARCHAR", "nullable": True, "description": "key"}]},  # nullable key
    {"columns": [{"name": "id", "classification": "none", "type": "VARCHAR", "nullable": False, "min": 0, "description": "key"}]},
    {"columns": [{"name": "id", "classification": "none", "type": "TEXT", "nullable": False, "description": "key"}]},  # type not allowed
    {"columns": [{"name": "_id", "classification": "none", "type": "VARCHAR", "nullable": False, "description": "key"}]},  # reserved name
    {"kind": "aggregate"},  # changed_keys only belong to row tables
    {"primary_key": ["id", "other"]},  # undeclared key column
])
def test_invalid_contracts_are_rejected(overrides):
    with pytest.raises(ValidationError):
        GoldContract.model_validate(_base(**overrides))


def test_definition_hash_changes_with_the_contract(gold_contracts):
    contract = gold_contracts["customer_profile"]
    changed = contract.model_copy(update={"description": "something else"})
    assert changed.definition_hash != contract.definition_hash
