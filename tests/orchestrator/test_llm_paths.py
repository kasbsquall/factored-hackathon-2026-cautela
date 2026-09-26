"""The LLM paths: schema-validated extraction with a recorded deterministic fallback, grounded replies with a
template fallback, masking before the adapter, and the confirmation token kept out of every prompt."""

from __future__ import annotations

import json

import pytest

from agent.orchestrator import replies
from tests.orchestrator.conftest import ScriptedAdapter, extraction


def _understand(result):
    return next(s for s in result.trail if s.step == "understand")


def test_llm_extraction_supplies_structured_cues(make_orchestrator, cases, login):
    case = cases["normal"]
    tx = case.transaction
    adapter = ScriptedAdapter(extracts=[extraction(amount=float(tx["amount"]), currency=tx["currency"],
                                                   date=tx["transaction_date"].date().isoformat(),
                                                   merchant=tx["merchant_name"])])
    orch = make_orchestrator(adapter)
    vague_words = f"del {tx['transaction_date'].day} de mayo pasado, {tx['merchant_name']}, {float(tx['amount']):.2f}"
    result = orch.turn(login(case), f"No reconozco esto {vague_words}", language="es")
    assert _understand(result).outcome == "llm"
    state = orch.store.get(result.conversation_id, case.customer_id)
    assert state.slots.date == tx["transaction_date"].date() and state.transaction_id == case.transaction_id
    assert result.llm["calls"] == 2 and result.llm["cost_usd"] == 0.0  # fake provider is priced at zero


@pytest.mark.parametrize("bad, reason", [
    ("not json at all", "LLMOutputError"),
    ({"intent": "dispute_charge"}, "LLMOutputError"),                       # required keys missing
    (extraction(customer_id="C000001"), "LLMOutputError"),                 # unknown field
    (extraction(intent="refund_now"), "LLMOutputError"),                   # not in the enum
    (TimeoutError("provider down"), "LLMUnavailable"),
])
def test_invalid_or_failed_extraction_falls_back_to_the_parser(make_orchestrator, cases, login, bad, reason):
    orch = make_orchestrator(ScriptedAdapter(extracts=[bad]))
    result = orch.turn(login(cases["normal"]), cases["normal"].opener("es"), language="es")
    step = _understand(result)
    assert step.outcome == "deterministic_parser" and step.detail["fallback_reason"] == reason
    assert result.stage == "awaiting_confirmation", "the fallback still resolves a clear description"


def test_record_reference_the_customer_did_not_write_is_dropped(make_orchestrator, cases, login, people):
    invented = "TX00000001"
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(record_ref=invented)]))
    result = orch.turn(login(cases["normal"]), "No reconozco un cargo", language="es")
    assert _understand(result).detail["record_ref"] is None
    assert not any(s.step == "tool.get_transaction" for s in result.trail)


def test_parser_escalation_signals_override_the_model(make_orchestrator, cases, login):
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(intent="dispute_charge", amount=5.0)]))
    result = orch.turn(login(cases["normal"]), "Quiero hablar con una persona, por favor", language="es")
    assert result.stage == "handed_off"
    assert result.handoff["transfer_reason"]["code"] == "customer_requested_human"
    assert _understand(result).detail["fallback_reason"] == "parser_escalation_over_llm:dispute_charge"


def test_grounded_llm_reply_is_used_and_ungrounded_one_is_not(make_orchestrator, cases, login):
    case = cases["normal"]
    wording = "Para encontrarlo, ¿me dices el monto, la fecha o el comercio del cargo?"
    orch = make_orchestrator(ScriptedAdapter(replies=[wording, "Listo, tu caso quedó abierto."]))
    token = login(case)
    first = orch.turn(token, "No reconozco un cargo en mi cuenta.", language="es")
    assert first.reply_source == "llm" and first.reply == wording
    second = orch.turn(token, case.opener("es"), first.conversation_id)
    assert second.reply_source == "template", "a confirmation reply must name the charge label"
    note = next(s for s in second.trail if s.step == "reply").detail["note"]
    assert note == "llm_reply_rejected:missing_required_mention"


@pytest.mark.parametrize("text, lang, reason", [
    ("", "es", "empty"),
    ("Te devolvemos 500 pesos mañana.", "es", "number_not_in_facts"),
    ("Você não precisa fazer nada, o seu caso foi aberto.", "es", "wrong_language"),
    ("El caso ya quedó.", "es", "missing_required_mention"),
    ("x" * 1000, "es", "too_long"),
])
def test_reply_checks(text, lang, reason):
    assert replies.check_grounded(text, "caso CASE-ABC 30/05/2026", ["CASE-ABC"] if reason ==
                                  "missing_required_mention" else [], lang) == reason


def test_confirmation_token_never_reaches_a_prompt_reply_trail_or_audit(rig, make_orchestrator, cases, login):
    case = cases["normal"]
    adapter = ScriptedAdapter()
    orch = make_orchestrator(adapter)
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    state = orch.store.get(first.conversation_id, case.customer_id)
    tokens = [state.pending.token]
    rig.clock.advance(minutes=16)  # expire, log in again: a second token is issued
    fresh = login(case)
    reissued = orch.confirm(fresh, first.conversation_id, first.confirmation["confirmation_id"])
    tokens.append(state.pending.token)
    done = orch.confirm(fresh, first.conversation_id, reissued.confirmation["confirmation_id"])
    assert done.stage == "resolved" and len(set(tokens)) == 2 and all(tokens)
    sent = adapter.everything_sent()
    assert adapter.received, "the model was called on every turn"
    visible = json.dumps([r.reply for r in (first, reissued, done)] + [r.confirmation for r in (first, reissued)]
                         + [s.as_dict() for s in state.trail], default=str)
    audit = json.dumps([r.model_dump() for r in rig.audit.records()], default=str)
    for secret in tokens + [token, fresh]:
        assert secret not in sent
        assert secret not in visible
        assert secret not in audit


def test_document_number_is_masked_before_the_adapter(make_orchestrator, cases, login):
    case = cases["normal"]
    adapter = ScriptedAdapter()
    orch = make_orchestrator(adapter)
    orch.turn(login(case), f"Mi documento es {case.document_number}. {case.opener('es')}", language="es")
    assert adapter.received and case.document_number not in adapter.everything_sent()


def test_reply_prompt_holds_only_the_facts_of_the_turn(make_orchestrator, cases, login):
    case = cases["normal"]
    adapter = ScriptedAdapter()
    orch = make_orchestrator(adapter)
    orch.turn(login(case), case.opener("pt"), language="pt")
    reply_prompts = [p for p in adapter.received if p.json_schema is None]
    assert reply_prompts and all("Portuguese" in p.system for p in reply_prompts)
    payload = json.loads(reply_prompts[-1].user)
    assert payload["message_kind"] == "confirm_open" and payload["language"] == "pt"
    assert case.customer_id not in reply_prompts[-1].user


def test_values_the_customer_did_not_state_are_dropped(make_orchestrator, cases, login):
    """Seen with qwen2.5:7b: 'No reconozco un cargo en mi cuenta' came back with today's date and PESOS."""
    filled = extraction(date="2026-06-01", currency="PESOS", merchant="Marketplace Uno", amount=500.0)
    orch = make_orchestrator(ScriptedAdapter(extracts=[filled]))
    result = orch.turn(login(cases["normal"]), "No reconozco un cargo en mi cuenta.", language="es")
    step = _understand(result)
    assert step.outcome == "llm"
    assert step.detail["fallback_reason"] == "dropped_unstated:amount,date,currency,merchant"
    assert result.stage == "clarifying" and not result.options, "with nothing stated it asks for details"


def test_stated_values_survive_grounding(make_orchestrator, cases, login):
    case = cases["normal"]
    tx = case.transaction
    stated = extraction(amount=float(tx["amount"]), currency=tx["currency"],
                        date=tx["transaction_date"].date().isoformat(), merchant=tx["merchant_name"])
    orch = make_orchestrator(ScriptedAdapter(extracts=[stated]))
    result = orch.turn(login(case), case.opener("es"), language="es")
    assert _understand(result).detail["fallback_reason"] is None


def test_a_quoted_reference_is_checked_before_an_out_of_scope_label(make_orchestrator, cases, login):
    """Seen with qwen2.5:7b: 'Es la transacción TX99999999.' was labeled out_of_scope."""
    orch = make_orchestrator(ScriptedAdapter(extracts=[extraction(intent="out_of_scope", record_ref="TX99999999")]))
    result = orch.turn(login(cases["normal"]), "Es la transacción TX99999999.", language="es")
    assert result.stage == "collecting" and result.handoff is None


def test_a_model_date_that_disagrees_with_the_text_is_dropped():
    """Seen with qwen2.5:7b: 'no dia 30 de maio' came back as 2026-05-31."""
    from datetime import date

    from agent.orchestrator.intent import from_llm
    text = "Não reconheço uma compra de 20 reais no dia 30 de maio."
    assert from_llm(extraction(date="2026-05-31"), text, date(2026, 5, 31))[1] == ["date"]
    assert from_llm(extraction(date="2026-05-30"), text, date(2026, 5, 31))[1] == []


def test_a_paraphrased_policy_reason_is_replaced_by_the_template(make_orchestrator, cases, login):
    """Seen with qwen2.5:7b: a synthetic-policy reason was reworded as 'pelo regulamento vigente'."""
    case = cases["human"]
    token = login(case)
    first = make_orchestrator().turn(token, case.opener("pt"), language="pt")
    label = first.confirmation["label"]
    orch = make_orchestrator(ScriptedAdapter(replies=[f"Vou registrar {label}. Pelo regulamento vigente, uma pessoa "
                                                      "vai analisar. Confirme no botão."]))
    result = orch.turn(login(case), case.opener("pt"), language="pt")
    assert result.reply_source == "template" and "regulamento" not in result.reply
