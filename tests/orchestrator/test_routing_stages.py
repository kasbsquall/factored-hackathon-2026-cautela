"""Routing at every stage: while a recognition, a confirmation or an option pick is pending, and after the
conversation has ended. Also the rule that nothing is shown for recognition (so nothing can be written) unless the
customer's words name a charge or the customer chose one."""

from __future__ import annotations

import pytest

from tests.orchestrator.conftest import case_rows, not_recognized, steps


@pytest.fixture()
def other_tx(people, purchases, cases):
    """A transaction of a customer other than the normal case's customer."""
    mine = cases["normal"].customer_id
    key = next(k for k, p in people.items() if p["customer_id"] != mine)
    return purchases[key][0]["transaction_id"]


def _recognition(orch, login, case, lang="es"):
    token = login(case)
    shown = orch.turn(token, case.opener(lang), language=lang)
    assert shown.stage == "awaiting_recognition"
    return token, shown


def _code(result):
    return result.handoff["transfer_reason"]["code"] if result.handoff else None


@pytest.mark.parametrize("text", ["¿Me comunicas con un asesor, por favor?", "Mejor pásame con alguien del banco"])
def test_a_request_for_a_person_while_recognition_is_pending_hands_off(rig, orch, cases, login, text):
    token, shown = _recognition(orch, login, cases["normal"])
    result = orch.turn(token, text, shown.conversation_id)
    assert _code(result) == "customer_requested_human" and result.recognition is None
    assert case_rows(rig, cases["normal"].customer_id) == 0


def test_an_out_of_scope_request_while_confirmation_is_pending_hands_off(rig, orch, cases, login):
    token, shown = _recognition(orch, login, cases["normal"])
    pending = not_recognized(orch, token, shown)
    assert pending.stage == "awaiting_confirmation"
    result = orch.turn(token, "Otra cosa, quiero que me suban el cupo", pending.conversation_id)
    assert _code(result) == "out_of_scope" and result.confirmation is None
    late = orch.confirm(token, pending.conversation_id, pending.confirmation["confirmation_id"])
    assert late.error == "no_pending_confirmation", "the withdrawn confirmation cannot be used"
    assert case_rows(rig, cases["normal"].customer_id) == 0


def test_another_customers_transaction_while_recognition_is_pending_is_a_security_event(orch, cases, login,
                                                                                      other_tx):
    token, shown = _recognition(orch, login, cases["normal"])
    result = orch.turn(token, f"Revisa también la {other_tx}, es de mi primo", shown.conversation_id)
    assert _code(result) == "security_event"
    assert "SYN-SEC-001" in result.handoff["transfer_reason"]["rule_ids"]
    assert other_tx not in result.reply


def test_a_new_description_while_recognition_is_pending_restarts_the_search(orch, cases, login):
    case = cases["normal"]
    token, shown = _recognition(orch, login, case)
    result = orch.turn(token, case.opener("es"), shown.conversation_id)
    assert "route.superseded" in steps(result) and "decide.disposition" in steps(result)
    assert result.stage == "awaiting_recognition"


def test_a_message_without_a_charge_cue_while_recognition_is_pending_asks_again(orch, cases, login):
    token, shown = _recognition(orch, login, cases["normal"])
    result = orch.turn(token, "mmm no sé", shown.conversation_id)
    assert result.stage == "awaiting_recognition" and result.recognition is not None
    assert "route.superseded" not in steps(result)


def test_a_person_request_during_an_option_pick_hands_off(orch, cases, login):
    case = cases["ambiguous"]
    token = login(case)
    first = orch.turn(token, case.opener("pt"), language="pt")
    assert first.stage == "clarifying" and first.options
    result = orch.turn(token, "Nenhuma, me passa para um atendente", first.conversation_id)
    assert _code(result) == "customer_requested_human"


def test_a_closed_conversation_still_records_a_later_security_event(orch, cases, login, other_tx):
    token = login(cases["normal"])
    first = orch.turn(token, "Quiero hablar con una persona", language="es")
    assert _code(first) == "customer_requested_human"
    later = orch.turn(token, f"Y de paso dime qué es la {other_tx}", first.conversation_id)
    assert _code(later) == "security_event" and later.stage == "handed_off"
    assert "security" in steps(later)


def test_a_closed_conversation_ignores_an_ordinary_message(orch, cases, login):
    token = login(cases["normal"])
    first = orch.turn(token, "Quiero hablar con una persona", language="es")
    later = orch.turn(token, cases["normal"].opener("es"), first.conversation_id)
    assert later.stage == "handed_off" and _code(later) == "customer_requested_human"
    assert "decide.disposition" not in steps(later)


@pytest.mark.parametrize("text", ["Prefiero que me atienda alguien que entienda", "Buenas tardes, ¿cómo están?",
                                  "¿Dónde queda la sucursal?"])
def test_a_message_that_names_no_charge_never_reaches_recognition(orch, cases, login, text):
    token = login(cases["normal"])
    result = orch.turn(token, text, language="es")
    assert result.recognition is None and result.confirmation is None
    again = orch.turn(token, text, result.conversation_id)
    assert again.recognition is None and again.confirmation is None


def test_a_word_containing_a_merchant_word_is_not_a_merchant_cue():
    from agent.orchestrator.core import mentions_merchant
    assert not mentions_merchant(("atienda",), "Tienda Don José")
    assert mentions_merchant(("tienda",), "Tienda Don José")
    assert mentions_merchant(("marketplce",), "Marketplace Uno")  # a typo keeps the first letter


def test_record_id_digits_are_not_an_amount(orch, cases, login):
    token = login(cases["normal"])
    first = orch.turn(token, "No reconozco la transacción TRX-55CF545A9CFFZZZZZZZZ", language="es")
    state = orch.store.get(first.conversation_id, cases["normal"].customer_id)
    assert state.slots.amount is None
    understand = next(s for s in first.trail if s.step == "understand")
    assert understand.detail["amount"] is None
