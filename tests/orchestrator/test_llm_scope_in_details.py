"""While the service waits for charge details, a message with charge cues stays a dispute detail even when the model
labels it an out-of-scope request (routing._detail_in_scope). Deterministic code around the model output: the
result is the parser's own reading, so the model never adds an action. Phrasings were written for these tests."""

from __future__ import annotations

from tests.orchestrator.conftest import ScriptedAdapter, extraction, steps

VAGUE = "Oi, tem uma cobrança no meu cartão que eu não reconheço"
DETAIL = "Foi uma transferência de 1700 dólares no dia 7 de maio."
AS_TRANSFER = extraction(intent="out_of_scope", topic="money_transfer", amount=1700.0, currency="USD")


def _understood(result) -> dict:
    return next(s for s in result.trail if s.step == "understand").detail


def test_a_charge_detail_answering_the_question_stays_a_dispute(make_orchestrator, cases, login):
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(), AS_TRANSFER]))
    token = login(cases["normal"])
    first = orch.turn(token, VAGUE, language="pt")
    assert first.stage == "clarifying" and not first.options
    second = orch.turn(token, DETAIL, first.conversation_id)
    assert _understood(second)["intent"] == "dispute_charge"
    assert _understood(second)["fallback_reason"] == "charge_detail_in_scope:money_transfer"
    assert "tool.list_recent_transactions" in steps(second)
    assert not (second.handoff and second.handoff["transfer_reason"]["code"] == "out_of_scope")


def test_the_first_message_keeps_the_model_label(make_orchestrator, cases, login):
    orch = make_orchestrator(ScriptedAdapter(extracts=[AS_TRANSFER]))
    result = orch.turn(login(cases["normal"]), DETAIL, language="pt")
    assert result.stage == "handed_off" and result.handoff["transfer_reason"]["code"] == "out_of_scope"


def test_an_explicit_request_while_waiting_for_details_still_escalates(make_orchestrator, cases, login):
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(), extraction(
        intent="out_of_scope", topic="money_transfer", amount=500.0, currency="USD")]))
    token = login(cases["normal"])
    first = orch.turn(token, "Hola, me salió un cobro que no reconozco", language="es")
    second = orch.turn(token, "Mejor quiero hacer una transferencia de 500 dólares a mi hermano",
                       first.conversation_id)
    assert second.stage == "handed_off" and second.handoff["transfer_reason"]["code"] == "out_of_scope"


def test_a_message_without_charge_cues_keeps_the_model_label(make_orchestrator, cases, login):
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(), extraction(
        intent="out_of_scope", topic="investment_advice")]))
    token = login(cases["normal"])
    first = orch.turn(token, "Hola, me salió un cobro que no reconozco", language="es")
    second = orch.turn(token, "Oye, y de paso, ¿qué fondo me recomiendas?", first.conversation_id)
    assert second.stage == "handed_off" and second.handoff["transfer_reason"]["code"] == "out_of_scope"
