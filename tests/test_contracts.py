from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from data_engineering.contracts.loader import (
    CONTRACTS_DIR,
    ColumnContract,
    dependency_order,
    load_contract,
    load_contracts,
)

ALL_TABLES = {
    "customers", "products", "branches", "service_agents", "marketing_campaigns", "transactions",
    "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events", "complaints",
    "campaign_sends", "daily_exchange_rates",
}


@pytest.fixture(scope="module")
def contracts():
    return load_contracts()


def test_all_thirteen_contracts_load(contracts):
    assert set(contracts) == ALL_TABLES


def test_partitioning_matches_dictionary(contracts):
    assert contracts["customers"].partitioning == "monthly_snapshot"
    assert contracts["products"].partitioning == "monthly_snapshot"
    assert contracts["service_agents"].partitioning == "monthly_snapshot"
    assert contracts["branches"].partitioning == "full_snapshot"
    assert contracts["marketing_campaigns"].partitioning == "full_snapshot"
    daily = {t for t, c in contracts.items() if c.partitioning == "daily"}
    assert daily == ALL_TABLES - {"customers", "products", "service_agents", "branches", "marketing_campaigns"}


def test_stated_ranges_are_encoded(contracts):
    assert (contracts["customers"].column("credit_score").min, contracts["customers"].column("credit_score").max) == (300, 850)
    assert contracts["transactions"].column("fraud_score").max == 100
    sentiment = contracts["call_center_interactions"].column("sentiment_score")
    assert (sentiment.min, sentiment.max) == (-1, 1)


def test_truncated_enum_reconciled_with_delivered_values(contracts):
    product_type = contracts["products"].column("product_type")
    assert product_type.enum_status == "decoded" and "truncated" in product_type.note
    assert product_type.observed_values == ["Seguro"]
    assert set(product_type.normalize.values()) <= set(product_type.allowed_values)
    assert product_type.severity_for("bad_enum") == "warn"


def test_every_normalization_target_is_an_accepted_value(contracts):
    for table, contract in contracts.items():
        for col in contract.columns:
            if col.normalize and col.allowed_values:
                assert set(col.normalize.values()) <= set(col.accepted_values), f"{table}.{col.name}"


def test_normalize_cannot_widen_a_value_list():
    with pytest.raises(ValidationError, match="outside the value list"):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=False, allowed_values=["Urban"], enum_status="listed",
                       normalize={"Urbana": "Urbano"}, severity="error", description="x")


def test_observed_values_need_a_note():
    with pytest.raises(ValidationError, match="note"):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=False, allowed_values=["Phone"], enum_status="listed",
                       observed_values=["Web"], severity="error", description="x")


def test_decoded_enums_only_warn_on_bad_values(contracts):
    for table, contract in contracts.items():
        for col in contract.columns:
            if col.enum_status == "decoded":
                assert col.severity_for("bad_enum") == "warn", f"{table}.{col.name}"


def test_composite_primary_key(contracts):
    assert contracts["daily_exchange_rates"].primary_key == ["date", "source_currency", "target_currency"]


def test_every_dictionary_foreign_key_is_declared(contracts):
    declared = {f"{t}.{c.name}": c.fk for t, ct in contracts.items() for c in ct.foreign_keys}
    # Dictionary "Foreign Key Relationships" section: 8 + 5 + 4 + 3 + 1 + 3 = 24 relationships.
    assert len(declared) == 24
    assert declared["complaints.origin_interaction_id"] == "call_center_interactions.interaction_id"


def test_dependency_order_puts_parents_first(contracts):
    order = dependency_order(contracts)
    for table, contract in contracts.items():
        for col in contract.foreign_keys:
            assert order.index(col.fk_table) < order.index(table)


def test_dependency_order_rejects_unknown_table(contracts):
    with pytest.raises(ValueError):
        dependency_order(contracts, ["nope"])


def test_primary_key_cannot_be_nullable():
    with pytest.raises(ValidationError):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=True, pk=True, severity="error", description="x")


def test_unknown_enum_requires_note():
    with pytest.raises(ValidationError):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=False, enum_status="unknown", profile=True,
                       severity="error", description="x")


def test_bad_fk_format_rejected():
    with pytest.raises(ValidationError):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=True, fk="customers", severity="warn", description="x")


def test_range_on_text_rejected():
    with pytest.raises(ValidationError):
        ColumnContract(name="x", classification="none", type="VARCHAR(10)", nullable=True, min=1, severity="warn", description="x")


def test_table_name_must_match_file(tmp_path: Path):
    source = CONTRACTS_DIR / "branches.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    target = tmp_path / "other.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_contract(target)


def test_duckdb_type_mapping(contracts):
    tx = contracts["transactions"]
    assert tx.column("transaction_id").duckdb_type == "VARCHAR"
    assert tx.column("amount").duckdb_type == "DECIMAL(15,2)"
    assert contracts["call_transcripts"].column("full_text").duckdb_type == "VARCHAR"
