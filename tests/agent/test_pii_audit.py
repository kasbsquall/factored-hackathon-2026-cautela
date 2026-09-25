"""PII masking and the append-only audit log."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime

import pytest

from agent.clock import FrozenClock
from agent.security.audit import AUDIT_RETENTION_DAYS, AuditLog
from agent.security.pii import mask_field, mask_mapping, mask_text


@pytest.mark.parametrize(("text", "must_not_contain", "must_contain"), [
    ("mi tarjeta 4111 1111 1111 1234", ["4111 1111"], ["1234"]),
    ("cuenta 4111111111111234", ["411111111111"], ["************1234"]),
    ("correo juan.perez@correo.example", ["juan.perez"], ["j***@correo.example"]),
    ("llamame al +57 300 123 4567", ["300 123"], ["+57 ******67"]),
    ("o al 55 1234 5678", ["1234 5678"], ["******78"]),
    ("mi DNI es 12.345.678", ["12.345.678"], ["[DOC]"]),
    ("CC 1023456789", ["1023456789"], ["[DOC]"]),
    ("CURP GAPM850101HDFRRR09", ["GAPM850101"], ["[DOC]"]),
    ("Me llamo Maria Lopez y no hice esa compra", ["Maria Lopez"], ["[NAME]"]),
    ("Meu nome e Joao Silva, nao reconheco", ["Joao Silva"], ["[NAME]"]),
], ids=["card_spaced", "account", "email", "intl_phone", "local_phone", "dni_dotted", "cc", "curp", "name_es",
        "name_pt"])
def test_mask_text_removes_pii(text, must_not_contain, must_contain):
    masked = mask_text(text)
    assert all(s not in masked for s in must_not_contain), masked
    assert all(s in masked for s in must_contain), masked


def test_known_names_are_masked_with_and_without_accents():
    masked = mask_text("Hola, soy de Bogota. Gonzalez aqui, la senora GONZÁLEZ pago", known_names=["González"])
    assert "Gonzalez" not in masked and "GONZÁLEZ" not in masked


def test_ids_amounts_and_dates_survive_masking():
    text = "TX00000012 por 1250.50 MXN el 01/05/2026 en Supermercado La Estrella"
    assert mask_text(text) == text


def test_mask_mapping_keeps_record_ids_and_masks_sensitive_keys():
    masked = mask_mapping({"transaction_id": "TX00000012", "document_number": "1023456789",
                           "customer_statement": "mi correo es ana@x.example", "nested": {"mobile_phone": "3001234567"}})
    assert masked["transaction_id"] == "TX00000012"
    assert masked["document_number"] == "*******789"
    assert "ana@" not in masked["customer_statement"]
    assert masked["nested"]["mobile_phone"] == "******67"


def test_mask_field_names_are_redacted():
    assert mask_field("first_name", "María") == "[REDACTED]"
    assert mask_field("product_number", "5500000000001234") == "************1234"


@pytest.fixture()
def clock():
    return FrozenClock(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))


def test_audit_record_stores_masked_args_and_keyed_hash_only(tmp_path, clock):
    log = AuditLog(secrets.token_bytes(48), tmp_path, clock)
    rec = log.record(trace_id="tr_1", step="guard", tool="get_transaction", outcome="denied",
                     args={"document_number": "1023456789", "note": "tarjeta 4111111111111234"})
    raw = (tmp_path / "audit-2026-06-01.jsonl").read_text(encoding="utf-8")
    assert "1023456789" not in raw and "4111111111111234" not in raw
    assert rec.args_hash and len(rec.args_hash) == 64
    other = AuditLog(secrets.token_bytes(48), None, clock)
    assert other.record(trace_id="t", step="s", outcome="o", args={"document_number": "1023456789"}).args_hash \
        != rec.args_hash  # keyed: the same input hashes differently under another key


def test_audit_log_is_hash_chained_and_detects_edits(tmp_path, clock):
    log = AuditLog(secrets.token_bytes(48), tmp_path, clock)
    for i in range(3):
        log.record(trace_id="tr_1", step=f"s{i}", outcome="ok", args={"i": i})
    assert log.verify_chain()
    stored = AuditLog.load(tmp_path / "audit-2026-06-01.jsonl")
    assert log.verify_chain(stored)
    edited = [stored[0], stored[1].model_copy(update={"outcome": "denied"}), stored[2]]
    assert not log.verify_chain(edited)
    assert not log.verify_chain([stored[0], stored[2]])  # a deleted record breaks the chain


def test_audit_log_file_is_append_only_jsonl(tmp_path, clock):
    log = AuditLog(secrets.token_bytes(48), tmp_path, clock)
    log.record(trace_id="a", step="s", outcome="ok")
    log.record(trace_id="b", step="s", outcome="ok")
    lines = (tmp_path / "audit-2026-06-01.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [0, 1]
    assert [r.trace_id for r in log.records("b")] == ["b"]


def test_retention_purges_only_whole_expired_day_files(tmp_path, clock):
    log = AuditLog(secrets.token_bytes(48), tmp_path, clock)
    log.record(trace_id="a", step="s", outcome="ok")
    old = tmp_path / "audit-2010-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    removed = log.purge_expired()
    assert removed == [old]
    assert (tmp_path / "audit-2026-06-01.jsonl").exists()
    assert AUDIT_RETENTION_DAYS == 3650
