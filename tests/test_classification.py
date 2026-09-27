"""Every contract column carries a data classification, and gold serving tables expose no direct identifier beyond
the documented allowlist (README "Data at rest")."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from data_engineering.contracts.loader import CONTRACTS_DIR, load_contract, load_contracts
from data_engineering.gold.contract import load_gold_contracts

# The only direct identifiers a gold serving table may hold, and why.
SERVING_DIRECT_ALLOWED = {
    ("customer_profile", "first_name"): "the 'First L.' display name, and masking the name in free text",
    ("customer_profile", "last_name"): "its initial in the display name, and masking the name in free text",
}
DIRECT = {
    "customers": {"document_number", "first_name", "last_name", "email", "mobile_phone", "landline_phone", "address"},
    "service_agents": {"first_name", "last_name", "email", "phone"},
    "call_transcripts": {"full_text", "customer_text", "agent_text", "mentioned_entities"},
    "complaints": {"description", "resolution"},
    "satisfaction_surveys": {"open_comments"},
}


@pytest.fixture(scope="module")
def silver():
    return load_contracts()


@pytest.fixture(scope="module")
def gold():
    return load_gold_contracts()


def test_every_silver_and_gold_column_is_classified(silver, gold):
    columns = [c for t in silver.values() for c in t.columns] + [c for t in gold.values() for c in t.columns]
    assert len(columns) > 400
    assert {c.classification for c in columns} == {"pii_direct", "pii_quasi", "sensitive_financial", "none"}


def test_a_contract_without_classification_does_not_load(tmp_path: Path):
    data = yaml.safe_load((CONTRACTS_DIR / "branches.yaml").read_text(encoding="utf-8"))
    del data["columns"][0]["classification"]
    target = tmp_path / "branches.yaml"
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError, match="classification"):
        load_contract(target)


def test_identity_contact_and_free_text_columns_are_direct(silver):
    for table, contract in silver.items():
        direct = {c.name for c in contract.columns if c.classification == "pii_direct"}
        assert direct == DIRECT.get(table, set()), table


def test_person_keys_and_date_of_birth_are_quasi_identifiers(silver):
    for contract in silver.values():
        for col in contract.columns:
            if col.name in ("customer_id", "agent_id"):
                assert col.classification == "pii_quasi", f"{contract.table}.{col.name}"
    assert silver["customers"].column("date_of_birth").classification == "pii_quasi"
    assert silver["products"].column("product_number").classification == "sensitive_financial"


def test_gold_serving_tables_hold_no_direct_identifier_outside_the_allowlist(gold):
    found = {(t, c.name) for t, contract in gold.items() if contract.purpose == "serving"
             for c in contract.columns if c.classification == "pii_direct"}
    assert found == set(SERVING_DIRECT_ALLOWED)
    for table, contract in gold.items():
        if contract.purpose == "analytics":
            assert not [c.name for c in contract.columns if c.classification == "pii_direct"], table


def test_gold_keeps_the_class_of_the_silver_column_it_copies(silver, gold):
    classes: dict[str, set[str]] = {}
    for contract in silver.values():
        for col in contract.columns:
            classes.setdefault(col.name, set()).add(col.classification)
    for table, contract in gold.items():
        for col in contract.columns:
            known = classes.get(col.name, set()) - {"none"}
            if known:
                assert col.classification in known, f"{table}.{col.name}"
