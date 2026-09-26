"""The three required cases end to end (normal, ambiguous or unsupported, human-required) in Spanish and
Portuguese, plus multi-turn state, missing and bad data, and the session gate."""

from __future__ import annotations

import pytest

from agent.handoff import validate_handoff
from tests.orchestrator.conftest import case_rows, steps

LANGS = ["es", "pt"]


@pytest.mark.parametrize("lang", LANGS)
def test_normal_case_is_resolved_and_verified(rig, orch, cases, login, lang):
    case = cases["normal"]
    token = login(case)
    first = orch.turn(token, case.opener(lang), language=lang)
    assert first.stage == "awaiting_confirmation" and first.language == lang
    assert first.confirmation and "token" not in first.confirmation
    assert case_rows(rig, case.customer_id) == 0, "nothing is written before the customer confirms"
    assert {"gate", "understand", "decide.disposition", "decide.policy", "confirm.request"} <= set(steps(first))
    policy = next(s for s in first.trail if s.step == "decide.policy")
    assert "MX-WINDOW-001" in policy.rule_ids and "SYN-CONFIRM-001" in policy.rule_ids

    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.stage == "resolved" and done.handoff is None
    assert done.case and done.case["verified"] is True
    assert done.case["case_id"] in done.reply
    verify = next(s for s in done.trail if s.step == "verify")
    assert verify.outcome == "verified"
    assert case_rows(rig, case.customer_id) == 1
    assert rig.cases.read_case(done.case["case_id"])["transaction_id"] == case.transaction_id
    records = rig.audit.records(done.trace_id)
    assert {"orchestrator.verify", "orchestrator.turn", "tool"} <= {r.step for r in records}
    assert rig.audit.verify_chain()


@pytest.mark.parametrize("lang", LANGS)
def test_ambiguous_case_asks_then_carries_state(rig, orch, cases, login, lang):
    case = cases["ambiguous"]
    token = login(case)
    first = orch.turn(token, case.opener(lang), language=lang)
    assert first.stage == "clarifying" and 2 <= len(first.options) <= 3
    assert all(o.label in first.reply for o in first.options), "the question lists the candidates"
    pick = "2" if lang == "es" else "a segunda"
    second = orch.turn(token, pick, first.conversation_id)
    chosen = first.options[1]
    state = orch.store.get(first.conversation_id, case.customer_id)
    assert state.transaction_id == chosen.record_id, "the pick resolves against the options of the earlier turn"
    assert second.stage in {"awaiting_confirmation", "handed_off", "abstained"}
    assert state.statements == [case.opener(lang)], "the customer did not have to repeat the description"


@pytest.mark.parametrize("lang", LANGS)
def test_unsupported_request_abstains_with_reason_and_transfers(orch, cases, login, lang):
    text = {"es": "Quiero que me aumenten el cupo de mi tarjeta.", "pt": "Quero aumentar o limite do cartão."}[lang]
    result = orch.turn(login(cases["normal"]), text, language=lang)
    assert result.stage == "handed_off"
    assert result.handoff["transfer_reason"] == {"code": "out_of_scope", "rule_ids": ["SYN-SCOPE-001"]}
    assert result.handoff["request"]["intent"] == "credit_limit_increase"


def test_declined_charge_is_explained_not_disputed(rig, orch, cases, login):
    case = cases["declined"]
    result = orch.turn(login(case), case.opener("es"), language="es")
    assert result.stage == "abstained" and result.handoff is None
    policy = next(s for s in result.trail if s.step == "decide.policy")
    assert "SYN-STATUS-002" in policy.rule_ids
    assert case_rows(rig, case.customer_id) == 0


@pytest.mark.parametrize("lang", LANGS)
def test_human_required_case_registers_for_review_and_hands_off(rig, orch, cases, login, lang):
    case = cases["human"]
    token = login(case)
    first = orch.turn(token, case.opener(lang), language=lang)
    assert first.stage == "awaiting_confirmation" and first.confirmation["review"] is True
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.stage == "handed_off"
    handoff = done.handoff
    validate_handoff(handoff)
    assert handoff["language"] == lang
    assert handoff["transfer_reason"]["code"] == "amount_above_threshold"
    assert "SYN-AMOUNT-001" in handoff["transfer_reason"]["rule_ids"]
    assert handoff["request"]["disputed_transaction_ids"] == [case.transaction_id]
    assert handoff["actions_taken"] == [{"action": "open_dispute_case", "status": "verified",
                                         "record_id": done.case["case_id"]}]
    assert all(":" in f["source"] for f in handoff["verified_facts"])
    assert any(f["source"] == f"get_case_status:{done.case['case_id']}" for f in handoff["verified_facts"])
    assert rig.cases.read_case(done.case["case_id"])["status"] == "pending_human_review"
    assert case.document_number not in str(handoff)


def test_missing_data_gets_a_targeted_question_then_bad_reference(orch, cases, login):
    token = login(cases["normal"])
    first = orch.turn(token, "No reconozco un cargo en mi cuenta.", language="es")
    assert first.stage == "clarifying" and "monto" in first.reply and "fecha" in first.reply
    second = orch.turn(token, "Es la transacción TX99999999.", first.conversation_id)
    assert second.stage == "collecting" and second.handoff is None
    assert any(s.step == "tool.get_transaction" and s.detail.get("error") == "not_found" for s in second.trail)


def test_amount_word_then_date_are_merged_across_turns(orch, cases, login):
    case = cases["normal"]
    amount = f"{float(case.transaction['amount']):.2f} {case.transaction['currency']}"
    when = case.transaction["transaction_date"]
    token = login(case)
    first = orch.turn(token, f"Me cobraron {amount} y no lo reconozco", language="es")
    state = orch.store.get(first.conversation_id, case.customer_id)
    assert state.slots.amount == pytest.approx(float(case.transaction["amount"]))
    if first.stage == "clarifying":
        orch.turn(token, f"Fue en {case.transaction['merchant_name']} el {when.day} de mayo", first.conversation_id)
        assert state.slots.amount == pytest.approx(float(case.transaction["amount"])), "earlier facts are kept"
        assert state.slots.date is not None


def test_bad_data_charge_goes_to_review_under_syn_data_001(orch, cases, login):
    case = cases["bad_data"]
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.handoff["transfer_reason"]["code"] == "policy_requires_review"
    assert "SYN-DATA-001" in done.handoff["transfer_reason"]["rule_ids"]
    assert any(f["fact"] == "USD amount unknown" for f in done.handoff["verified_facts"])


def test_clarification_is_bounded(orch, cases, login):
    token = login(cases["normal"])
    result = orch.turn(token, "Hola, tengo un problema.", language="es")
    for _ in range(3):
        if result.stage == "handed_off":
            break
        result = orch.turn(token, "No sé, algo raro.", result.conversation_id)
    assert result.stage == "handed_off"
    assert result.handoff["transfer_reason"]["code"] == "low_confidence"


def test_customer_can_decline_and_nothing_is_written(rig, orch, cases, login):
    case = cases["normal"]
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"], accept=False)
    assert done.stage == "closed" and case_rows(rig, case.customer_id) == 0
    again = orch.turn(token, "Hola otra vez", first.conversation_id)
    assert again.stage == "closed"


def test_message_while_confirmation_is_pending(orch, cases, login):
    case = cases["normal"]
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    reminder = orch.turn(token, "¿Sigues ahí?", first.conversation_id)
    assert reminder.stage == "awaiting_confirmation" and reminder.confirmation is not None
    human = orch.turn(token, "Quiero hablar con una persona", first.conversation_id)
    assert human.stage == "handed_off"
    assert human.handoff["transfer_reason"]["rule_ids"] == ["SYN-HUMAN-001"]


# ---- session gate ------------------------------------------------------------------------------------------
def test_expired_session_requires_login_and_resumes_without_repeating(rig, orch, cases, login):
    case = cases["normal"]
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    rig.clock.advance(minutes=16)
    late = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert late.stage == "auth_required" and late.error == "session_expired"
    assert case_rows(rig, case.customer_id) == 0
    fresh = login(case)
    reissued = orch.confirm(fresh, first.conversation_id, first.confirmation["confirmation_id"])
    assert reissued.stage == "awaiting_confirmation", "the old confirmation was bound to the old session"
    assert reissued.confirmation["confirmation_id"] != first.confirmation["confirmation_id"]
    done = orch.confirm(fresh, first.conversation_id, reissued.confirmation["confirmation_id"])
    assert done.stage == "resolved" and case_rows(rig, case.customer_id) == 1


@pytest.mark.parametrize("token", ["", "garbage", "a.b", "12345678"])
def test_invalid_tokens_never_reach_a_step(rig, orch, token):
    result = orch.turn(token, "No reconozco un cargo de 100 pesos")
    assert result.stage == "auth_required" and result.error == "session_invalid"
    assert result.conversation_id is None and result.trail == []


def test_conversation_of_another_customer_is_not_found(orch, cases, login):
    first = orch.turn(login(cases["normal"]), cases["normal"].opener("es"), language="es")
    other = login(cases["human"])
    result = orch.turn(other, "continúa", first.conversation_id)
    assert result.error == "conversation_not_found" and result.reply == ""
    confirm = orch.confirm(other, first.conversation_id, first.confirmation["confirmation_id"])
    assert confirm.error == "conversation_not_found"


def test_wrong_confirmation_id_is_refused(orch, cases, login):
    token = login(cases["normal"])
    first = orch.turn(token, cases["normal"].opener("es"), language="es")
    result = orch.confirm(token, first.conversation_id, "not-the-id")
    assert result.error == "no_pending_confirmation"


def test_every_turn_writes_audit_with_latency_and_llm_fields(rig, orch, cases, login):
    result = orch.turn(login(cases["normal"]), cases["normal"].opener("pt"), language="pt")
    turn = [r for r in rig.audit.records(result.trace_id) if r.step == "orchestrator.turn"]
    assert len(turn) == 1 and turn[0].latency_ms > 0
    assert {"llm_calls", "llm_cost_usd", "reply_source"} <= set(turn[0].masked_args)
    assert all(r.trace_id == result.trace_id for r in rig.audit.records(result.trace_id))
