"""Idea 1 and 2 of the product: the customer sees why each candidate charge matched and the charge's verified
merchant evidence, and can stop before any dispute when they recognize it.

Everything shown must come from tools (get_transaction, list_recent_transactions, get_customer_profile) or from the
ranker features that fired; a recognized charge ends the conversation with no write and an audit record.
"""

from __future__ import annotations

from datetime import date

import pytest

from agent.orchestrator import evidence, fmt
from tests.orchestrator.conftest import ScriptedAdapter, case_rows, not_recognized, steps

CHARGE_FIELDS = {"transaction_date", "amount", "currency", "merchant_name", "merchant_category", "category", "channel",
                 "city", "country", "card_type", "card_last4", "transaction_type", "transaction_status"}
TOOL_FIELD = {"transaction_date": "transaction_date", "amount": "amount", "currency": "currency",
              "merchant_name": "merchant_name", "merchant_category": "merchant_category",
              "category": "transaction_category", "channel": "channel", "city": "transaction_city",
              "country": "transaction_country", "transaction_type": "transaction_type",
              "transaction_status": "transaction_status"}


def _tool_view(rig, token: str, transaction_id: str) -> dict:
    result = rig.service.execute("get_transaction", {"transaction_id": transaction_id}, token, rig.rid())
    assert result.ok
    return result.data


def _assert_charge_is_tool_data(charge: dict, view: dict) -> None:
    assert set(charge) == CHARGE_FIELDS
    for field, source in TOOL_FIELD.items():
        assert charge[field] == view[source], field


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_recognition_step_shows_verified_charge_and_fired_reasons(rig, orch, cases, login, lang):
    case = cases["normal"]
    token = login(case)
    asked = orch.turn(token, case.opener(lang), language=lang)
    assert asked.stage == "awaiting_recognition" and asked.confirmation is None
    check = asked.recognition
    _assert_charge_is_tool_data(check["charge"], _tool_view(rig, token, case.transaction_id))
    codes = [r["code"] for r in check["reasons"]]
    # the opener states the exact amount, the day and the merchant: those three features fired
    assert {"amount_exact", "date_same_day", "merchant_named"} <= set(codes)
    assert all(r["label"] == evidence.reason(r["code"], lang, r["value"])["label"] for r in check["reasons"])
    assert fmt.label_text(check["label"], lang) in asked.reply
    fired = next(s for s in asked.trail if s.step == "decide.reasons")
    assert fired.detail["reasons"][case.transaction_id] == codes
    policy = next(s for s in asked.trail if s.step == "tool.get_dispute_policy")
    assert policy.outcome == "ok"
    assert check["claim_window"]["rule_id"] == "MX-WINDOW-001" and date.fromisoformat(check["claim_window"]["deadline"])
    assert case_rows(rig, case.customer_id) == 0


def test_recognized_charge_ends_without_a_dispute_and_is_audited(rig, orch, cases, login):
    case = cases["normal"]
    token = login(case)
    asked = orch.turn(token, case.opener("es"), language="es")
    done = orch.recognize(token, asked.conversation_id, asked.recognition["recognition_id"], recognized=True)
    assert done.stage == "recognized" and done.confirmation is None and done.case is None and done.handoff is None
    assert done.recognition is None and fmt.label_text(asked.recognition["label"], "es") in done.reply
    assert "confirm.request" not in steps(done) and not any(s.step.startswith("tool.") for s in done.trail)
    assert case_rows(rig, case.customer_id) == 0
    answer = [r for r in rig.audit.records(done.trace_id) if r.step == "orchestrator.recognize.answer"]
    assert len(answer) == 1 and answer[0].outcome == "recognized"
    later = orch.turn(token, "Hola otra vez", asked.conversation_id)
    assert later.stage == "recognized" and "ya terminó" in later.reply, "a final stage takes no new action"
    again = orch.recognize(token, asked.conversation_id, asked.recognition["recognition_id"], recognized=False)
    assert again.error == "no_pending_recognition", "an answered question cannot be answered again"


def test_confirmation_carries_the_same_evidence_and_the_claim_deadline(rig, orch, cases, login):
    case = cases["normal"]
    token = login(case)
    asked = orch.turn(token, case.opener("pt"), language="pt")
    first = not_recognized(orch, token, asked)
    assert first.confirmation["charge"] == asked.recognition["charge"]
    assert first.confirmation["reasons"] == asked.recognition["reasons"]
    policy = rig.service.execute("get_dispute_policy", {"transaction_id": case.transaction_id}, token, rig.rid())
    facts = policy.data["decision"]["facts"]
    assert first.confirmation["claim_window"] == {"rule_id": facts["window_rule"], "deadline": facts["window_deadline"]}
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.case["claim_window"] == first.confirmation["claim_window"]


def test_every_option_carries_charge_data_and_only_reasons_that_fired(rig, orch, cases, login):
    case = cases["ambiguous"]
    token = login(case)
    first = orch.turn(token, case.opener("es"), language="es")
    assert first.stage == "clarifying" and first.options
    fired = next(s for s in first.trail if s.step == "decide.reasons").detail["reasons"]
    for option in first.options:
        _assert_charge_is_tool_data(option.charge, _tool_view(rig, token, option.record_id))
        assert [r["code"] for r in option.reasons] == fired[option.record_id]
        assert all(r["label"] for r in option.reasons)
    assert not any(r["code"] == "only_fit" for o in first.options for r in o.reasons), \
        "several candidates were shown, so none is the only fit"
    picked = orch.turn(token, "1", first.conversation_id)
    if picked.stage == "awaiting_recognition":
        codes = [r["code"] for r in picked.recognition["reasons"]]
        assert codes[-1] == "customer_selected" and codes[:-1] == fired[first.options[0].record_id]


def test_a_message_while_the_question_is_open_repeats_it(orch, cases, login):
    case = cases["normal"]
    token = login(case)
    asked = orch.turn(token, case.opener("es"), language="es")
    reminder = orch.turn(token, "¿Y ahora?", asked.conversation_id)
    assert reminder.stage == "awaiting_recognition"
    assert reminder.recognition["recognition_id"] == asked.recognition["recognition_id"]
    assert fmt.label_text(asked.recognition["label"], "es") in reminder.reply
    human = orch.turn(token, "Quiero hablar con una persona", asked.conversation_id)
    assert human.stage == "handed_off" and human.recognition is None


def test_recognition_answers_are_gated(rig, orch, cases, login):
    case = cases["normal"]
    token = login(case)
    asked = orch.turn(token, case.opener("es"), language="es")
    wrong = orch.recognize(token, asked.conversation_id, "rc_not_the_id", recognized=True)
    assert wrong.error == "no_pending_recognition"
    other = orch.recognize(login(cases["human"]), asked.conversation_id, asked.recognition["recognition_id"], True)
    assert other.error == "conversation_not_found"
    rig.clock.advance(minutes=16)
    late = orch.recognize(token, asked.conversation_id, asked.recognition["recognition_id"], recognized=False)
    assert late.stage == "auth_required" and late.error == "session_expired"
    fresh = login(case)
    first = orch.recognize(fresh, asked.conversation_id, asked.recognition["recognition_id"], recognized=False)
    assert first.stage == "awaiting_confirmation", "the question survives a new login of the same customer"


def test_model_text_never_reaches_the_evidence(make_orchestrator, rig, cases, login):
    case = cases["normal"]
    adapter = ScriptedAdapter(replies=["Es un cargo de Tienda Falsa por 1 peso, ¿lo reconoces?"])
    orch = make_orchestrator(adapter)
    token = login(case)
    asked = orch.turn(token, case.opener("es"), language="es")
    _assert_charge_is_tool_data(asked.recognition["charge"], _tool_view(rig, token, case.transaction_id))
    assert "Tienda Falsa" not in str(asked.recognition)
    assert asked.reply_source == "template", "the model reply did not name the charge label, so it was rejected"


# ---- evidence helpers, on a synthetic pool --------------------------------------------------------------------
POOL = [
    {"transaction_id": "T1", "transaction_date": "2026-05-20T10:00:00", "amount": 104.0, "currency": "MXN",
     "merchant_name": "Farmacia San Rafael", "channel": "POS", "transaction_type": "Purchase",
     "transaction_status": "Approved", "transaction_city": "Mérida", "product_id": "P1"},
    {"transaction_id": "T2", "transaction_date": "2026-05-02T10:00:00", "amount": 480.0, "currency": "MXN",
     "merchant_name": "Cine Plaza Mayor", "channel": "App", "transaction_type": "Purchase",
     "transaction_status": "Approved", "transaction_city": "Mérida", "product_id": "P2"},
]


def test_amount_and_date_distances_are_reported_with_their_values():
    found = evidence.match_reasons("no reconozco 100 pesos", date(2026, 5, 31),
                                   {"amount": 100.0, "date_hint": date(2026, 5, 18), "date_tolerance_days": 1},
                                   POOL, ["T1", "T2"], "es", 90)
    t1 = {r["code"]: r for r in found["T1"]}
    assert t1["amount_close"]["value"] == 4 and t1["amount_close"]["label"] == "Monto a 4% del que indicaste"
    assert t1["date_within_days"]["value"] == 2 and "2 días" in t1["date_within_days"]["label"]
    assert t1["only_fit"]["label"].startswith("Único cargo de los últimos 90 días")
    assert found["T2"] == [], "a charge that fits no cue gets no reason"


def test_a_period_is_reported_as_a_period_not_as_a_day():
    """'la semana pasada' is a period: the reason must not claim the customer named a date."""
    found = evidence.match_reasons("no reconozco una compra", date(2026, 5, 31),
                                   {"date_hint": date(2026, 5, 21), "date_tolerance_days": 3}, POOL, ["T1"], "pt", 90)
    assert [r["code"] for r in found["T1"]][0] == "date_in_range"
    assert found["T1"][0]["label"] == "Dentro do período que você mencionou"


def test_card_digits_come_only_from_card_products():
    products = [{"product_id": "P1", "product_type": "Debit Card", "product_number_masked": "**** **** **** 4821"},
                {"product_id": "P2", "product_type": "Savings Account", "product_number_masked": "******9012"}]
    cards = evidence.card_digits(products)
    assert cards == {"P1": {"card_type": "Debit Card", "card_last4": "4821"}}
    charge = evidence.charge_details(POOL[0], cards)
    assert charge["card_last4"] == "4821" and charge["merchant_category"] is None
    assert evidence.charge_details(POOL[1], cards)["card_last4"] is None
