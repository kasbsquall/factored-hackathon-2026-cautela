"""Handoff builder: schema validation, PII masking, and a handoff built from a real tool trace."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.handoff import ActionTaken, Fact, HandoffValidationError, build_handoff, validate_handoff

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _handoff(**overrides):
    base = dict(trace_id="tr_1", language="es", customer_ref="session:abc", intent="unrecognized_charge",
                summary="Cliente no reconoce un cargo de 1250.00 MXN", reason_code="amount_above_threshold",
                rule_ids=["SYN-AMOUNT-001", "MX-WINDOW-001"], created_at=NOW,
                verified_facts=[Fact("Cargo de 1250.00 MXN el 2026-05-20", "get_transaction:TX00000042")],
                actions_taken=[ActionTaken("open_dispute_case", "verified", "CASE-1")],
                open_questions=["El cliente conserva la tarjeta?"], disputed_transaction_ids=["TX00000042"])
    return build_handoff(**{**base, **overrides})


def test_valid_handoff_passes_schema():
    doc = _handoff()
    validate_handoff(doc)
    assert doc["transfer_reason"] == {"code": "amount_above_threshold",
                                      "rule_ids": ["MX-WINDOW-001", "SYN-AMOUNT-001"]}
    assert doc["request"]["disputed_transaction_ids"] == ["TX00000042"]


def test_portuguese_handoff_is_valid():
    assert _handoff(language="pt", summary="Cliente nao reconhece a compra")["language"] == "pt"


@pytest.mark.parametrize("overrides", [
    {"language": "en"},
    {"reason_code": "because_the_model_said_so"},
    {"actions_taken": [ActionTaken("open_dispute_case", "done")]},
    {"confidence": 1.5},
], ids=["language", "reason_code", "action_status", "confidence"])
def test_invalid_handoff_is_refused(overrides):
    with pytest.raises(HandoffValidationError):
        _handoff(**overrides)


def test_extra_fields_and_bad_dates_are_refused():
    doc = _handoff()
    with pytest.raises(HandoffValidationError):
        validate_handoff({**doc, "raw_transcript": "..."})
    with pytest.raises(HandoffValidationError):
        validate_handoff({**doc, "created_at": "yesterday"})


def test_free_text_is_masked_and_summary_trimmed():
    doc = _handoff(summary="Soy Ana Ruiz, tarjeta 4111111111111234, correo ana@x.example. " + "x" * 600,
                   open_questions=["Confirmar telefono +57 300 123 4567"], known_names=["Ruiz"])
    text = str(doc)
    assert "4111111111111234" not in text and "ana@x.example" not in text and "Ruiz" not in text
    assert "300 123 4567" not in text
    assert len(doc["request"]["summary"]) <= 500


def test_handoff_from_a_real_trace(rig, people, purchases):
    high = [p for p in purchases["alice"] if (p["amount_usd"] or 0) >= 450]
    if not high:
        pytest.skip("no high-amount purchase for this fixture customer")
    alice = rig.login(people["alice"])
    tx_id = high[0]["transaction_id"]
    policy = rig.call("get_dispute_policy", {"transaction_id": tx_id}, alice).data["decision"]
    case = rig.confirm_and_call("open_dispute_case", {"transaction_id": tx_id, "idempotency_key": "trace-key-01"},
                                alice)
    session = rig.identity.validate(alice)
    doc = build_handoff(
        trace_id=case.trace_id, language="es", customer_ref=session.customer_ref, intent="unrecognized_charge",
        summary="No reconoce el cargo", reason_code="amount_above_threshold",
        rule_ids=[h["rule_id"] for h in policy["rules_fired"]], created_at=rig.clock(),
        verified_facts=[Fact(f"Monto USD {policy['facts']['amount_usd']}", f"get_dispute_policy:{tx_id}")],
        actions_taken=[ActionTaken("open_dispute_case", case.verification, case.data["case_id"])],
        disputed_transaction_ids=[tx_id])
    assert doc["actions_taken"][0]["status"] == "verified"
    assert people["alice"]["customer_id"] not in str(doc)
