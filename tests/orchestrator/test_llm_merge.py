"""Merging the model's extraction with the parser (intent.merge), the reply guard's tolerance, and the service-side
reading of the learned disposition (abstain only when no_match is the most likely class)."""

from __future__ import annotations

import string
from datetime import date

import pytest

from agent.orchestrator import replies
from agent.orchestrator.disposition import LearnedDisposition
from agent.orchestrator.intent import from_llm, merge, parse_intent
from tests.orchestrator.conftest import extraction

TODAY = date(2026, 5, 20)


def _merged(text: str, **model_values):
    model, _ = from_llm(extraction(**model_values), text, TODAY)
    return merge(model, parse_intent(text, TODAY), text)


def test_the_parsers_date_is_kept_when_the_model_omits_it():
    found, kept = _merged("No reconozco un cargo de 250 pesos del 12 de mayo", amount=250.0, date=None)
    assert found.date == date(2026, 5, 12) and found.date_tolerance_days == 0 and "date" in kept


@pytest.mark.parametrize("text, model_amount, expected", [
    ("Me cobraron 1,7 millones de pesos que no reconozco", 1.7, 1_700_000.0),
    ("Un cargo de 30 lucas que no hice", 30.0, 30_000.0),
    ("Me sacaron como 2 palos de la cuenta, no fui yo", 2.0, 2_000_000.0),
    ("Uma cobrança de 3 mil reais que eu não fiz", 3.0, 3000.0),
])
def test_magnitude_words_follow_the_parser(text, model_amount, expected):
    found, kept = _merged(text, amount=model_amount)
    assert found.amount == expected and "amount" in kept


def test_the_model_may_add_a_value_the_parser_did_not_read():
    found, kept = _merged("No reconozco lo de Marketplace Uno", merchant="Marketplace Uno")
    assert found.merchant == "Marketplace Uno" and kept == []


def test_a_disputed_transfer_stays_a_dispute():
    text = "Me entró una transferencia rara ayer y no sé de dónde salió"
    found, kept = _merged(text, intent="out_of_scope", topic="money_transfer")
    assert found.intent == "dispute_charge" and found.topic is None and "intent" in kept


@pytest.mark.parametrize("text", [
    "¿Qué es ese débito de la semana pasada?",
    "O que é essa transferência que entrou ontem?",
])
def test_a_question_about_a_charge_stays_a_dispute(text):
    found, kept = _merged(text, intent="out_of_scope", topic="other_customer_request")
    assert found.intent == "dispute_charge" and "intent" in kept


def test_a_transfer_request_stays_out_of_scope():
    found, _ = _merged("Quiero hacer una transferencia a mi hermana", intent="out_of_scope", topic="money_transfer")
    assert (found.intent, found.topic) == ("out_of_scope", "money_transfer")


def test_record_id_digits_never_ground_a_model_amount():
    model, dropped = from_llm(extraction(amount=55.0), "Reclamo la TRX-55CF545A9CFFZZZZZZZZ", TODAY)
    assert model.amount is None and dropped == ["amount"]


# ---- reply guard --------------------------------------------------------------------------------------------
def test_a_policy_reason_may_start_a_sentence_with_a_capital_letter():
    reason = replies.reason_text("amount_above_threshold", "es")
    text = f"{reason[0].upper()}{reason[1:]}. Te paso con una persona del banco."
    assert replies.check_grounded(text, text, [reason], "es") is None


def test_a_reworded_policy_reason_is_still_rejected():
    reason = replies.reason_text("amount_above_threshold", "pt")
    text = "Vou te passar para uma pessoa do banco, pois pelo valor a análise é feita por uma pessoa."
    assert replies.check_grounded(text, text, [reason], "pt") == "missing_required_mention"


@pytest.mark.parametrize("lang", ["es", "pt"])
def test_every_template_passes_its_own_language_check(lang):
    for kind, texts in replies.TEMPLATES.items():
        fields = {k: "X" for _, k, _, _ in string.Formatter().parse(texts[lang]) if k}
        assert replies._language_ok(texts[lang].format(**fields), lang), kind
    for code, reason in replies.REASONS[lang].items():
        assert replies._language_ok(replies.render_template("handed_off", lang, {"reason": reason, "case": ""}),
                                    lang), code


# ---- learned disposition, service reading ---------------------------------------------------------------------
class _Ranker:
    def rank(self, inp, candidates):
        return [(c["transaction_id"], 0.9 - 0.1 * i) for i, c in enumerate(candidates)]


class _Decider:
    name = "stub"

    def __init__(self, probabilities):
        self.p = probabilities

    def proba(self, inp, candidates, ranked):
        return self.p

    def decide_case(self, inp, candidates, ranked):  # the fitted rule: abstain once P(no_match) >= 0.0464
        if self.p["no_match"] >= 0.0464:
            return self.p["match"], {"decision": "abstain", "top_k": []}
        return self.p["match"], {"decision": "act", "top_k": [ranked[0][0]]}


POOL = [{"transaction_id": f"T{i}"} for i in range(4)]


@pytest.mark.parametrize("probabilities, decision", [
    ({"match": 0.88, "ambiguous": 0.01, "no_match": 0.11}, "clarify"),
    ({"match": 0.01, "ambiguous": 0.92, "no_match": 0.07}, "clarify"),
    ({"match": 0.15, "ambiguous": 0.01, "no_match": 0.84}, "escalate"),
    ({"match": 0.97, "ambiguous": 0.01, "no_match": 0.02}, "resolve"),
])
def test_abstain_becomes_clarify_unless_no_match_is_most_likely(probabilities, decision):
    got = LearnedDisposition(_Ranker(), _Decider(probabilities)).decide("x", TODAY, {}, POOL)
    assert got.decision == decision
    if decision == "clarify":
        assert got.top_k == ["T0", "T1", "T2"] and got.note == "abstain_to_clarify"
