"""The request lexicon (agent/orchestrator/lexicon.py) on phrasings written for this test, not taken from any
evaluation suite: requests for a person and out-of-scope requests in regional Spanish and Brazilian Portuguese, and
dispute descriptions that share their words and must stay disputes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agent.orchestrator import lexicon
from agent.orchestrator.intent import parse_intent, plain

TODAY = date(2026, 5, 20)

HUMAN = [
    "¿Me comunica con un operador, por favor?",                        # es, formal
    "Pásame con alguien que sepa, porfa",                               # es, informal
    "Quiero que me atienda una persona y no una máquina",
    "Necesito un ejecutivo que vea mi caso",                            # CL, PE
    "¿Hay algún asesor disponible? Quisiera conversar con un asesor",
    "Derívame con tu supervisor",
    "No quiero hablar con un bot",
    "Me gustaría atención humana",
    "Póngame con el gerente de la sucursal",
    "¿Se puede hablar con un ser humano?",
    "Quero conversar com um atendente de verdade",                      # pt-BR
    "Me passa para alguém de carne e osso",
    "Tem como falar com o meu gerente?",
    "Preciso de um consultor, não de robô",
    "Me transfira para um humano, por favor",
    "Não quero falar com máquina",
]
NOT_HUMAN = [
    "Alguien usó mi tarjeta para transferir a otra persona 300 pesos",  # a transfer to someone, not a request
    "La persona que me atendió en la tienda me cobró dos veces",
    "Hay un cargo del gerente de la tienda que no reconozco",
    "Uma pessoa desconhecida fez uma compra no meu cartão",
    "Não reconheço uma cobrança de 80 reais da loja do shopping",
]

OUT_OF_SCOPE = [
    ("¿Me suben el cupo de la tarjeta? Lo necesito para el viaje", "credit_limit_increase"),
    ("Quiero más límite en mi tarjeta", "credit_limit_increase"),
    ("Solicito una ampliación de cupo", "credit_limit_increase"),
    ("Quero mais limite no cartão", "credit_limit_increase"),
    ("¿Cómo hago para sacar un préstamo?", "loan_application"),
    ("Necesito un crédito hipotecario", "loan_application"),
    ("Quero fazer um empréstimo consignado", "loan_application"),
    ("¿Me prestan 5 mil pesos hasta fin de mes?", "loan_application"),
    ("Necesito mandar plata a mi hermano en Rosario", "money_transfer"),
    ("¿Cómo puedo hacer un giro al exterior?", "money_transfer"),
    ("Preciso fazer um pix de 200 reais", "money_transfer"),
    ("Reembólsenme ya lo que pagué", "immediate_refund"),
    ("Quero o estorno agora", "immediate_refund"),
    ("Necesito cambiar el número de celular registrado", "change_personal_data"),
    ("Quero atualizar meu endereço", "change_personal_data"),
    ("Quiero dar de baja mi cuenta", "account_closure"),
    ("Quero fechar a minha conta corrente", "account_closure"),
    ("¿Conviene abrir un CDT o un plazo fijo?", "investment_advice"),
    ("Quero investir em renda fixa", "investment_advice"),
    ("¿A qué hora abren los sábados?", "other_customer_request"),
    ("¿Dónde queda la oficina más cercana?", "other_customer_request"),
    ("Necesito una constancia de saldo para la embajada", "other_customer_request"),
    ("Olvidé mi clave de la app", "other_customer_request"),
    ("Qual é o meu saldo?", "other_customer_request"),
    ("Preciso de um comprovante de residência do banco", "other_customer_request"),
]
DISPUTES = [  # overlap words (transferencia, devolver, préstamo, límite, horario) inside a dispute
    "Me cayó una transferencia de 1.500 pesos que no hice",
    "Apareceu uma transferência de 300 reais que eu não fiz",
    "No reconozco un cargo de 400 pesos, quiero que me devuelvan el dinero",
    "Vi en mi extracto una cuota de préstamo que no reconozco",
    "Me cobraron una comisión por exceder el límite y yo no fui",
    "No reconozco una compra de 90 dólares hecha fuera de horario el 3 de mayo",
    "Não reconheço um pagamento de 45 reais de ontem",
    "¿Qué sería esta transferencia de 2.000 pesos del lunes?",
    "O que é esse pagamento no meu extrato?",
]


@pytest.mark.parametrize("text", HUMAN)
def test_requests_for_a_person_are_recognized(text):
    assert lexicon.asks_for_human(plain(text)), text
    assert parse_intent(text, TODAY).intent == "request_human"


@pytest.mark.parametrize("text", NOT_HUMAN)
def test_a_person_mentioned_in_a_dispute_is_not_a_request(text):
    assert lexicon.asks_for_human(plain(text)) is None, text


@pytest.mark.parametrize("text, topic", OUT_OF_SCOPE)
def test_out_of_scope_requests_get_their_topic(text, topic):
    found = parse_intent(text, TODAY)
    assert (found.intent, found.topic) == ("out_of_scope", topic), text


@pytest.mark.parametrize("text", DISPUTES)
def test_disputes_that_share_topic_words_stay_disputes(text):
    found = parse_intent(text, TODAY)
    assert found.intent == "dispute_charge", (text, found.topic)
    assert lexicon.dispute_signal(plain(text))


@pytest.mark.parametrize("text, is_charge", [
    ("algo raro en el cajero, no lo reconosco", True),       # misspelled verb
    ("nao reconheso isso que saiu no app", True),
    ("un débito en la app", True),
    ("¿dónde descargo la app?", False),
    ("la sucursal del centro", False),
])
def test_a_channel_counts_only_when_said_about_a_charge(text, is_charge):
    assert lexicon.mentions_charge(plain(text)) is is_charge


def test_an_explicit_request_wins_over_a_dispute_word_unless_its_topic_overlaps_disputes():
    switch = parse_intent("Olvídate del cargo, lo que quiero es cerrar mi cuenta", TODAY)
    assert (switch.intent, switch.topic) == ("out_of_scope", "account_closure")
    refund = parse_intent("Devuélvanme el dinero de una vez, no quiero trámites", TODAY)
    assert (refund.intent, refund.topic) == ("out_of_scope", "immediate_refund")
    disputed = parse_intent("Devuélvanme el dinero de ese cobro que no reconozco", TODAY)
    assert disputed.intent == "dispute_charge"


def test_the_lexicon_flags_no_validation_dispute_description():
    """Every validation description is a dispute: none may read as a person request or an out-of-scope topic."""
    path = Path(__file__).resolve().parents[2] / "eval" / "cases" / "disputes" / "val.jsonl"
    if not path.exists():
        pytest.skip("validation cases not built")
    texts = [json.loads(line)["description"] for line in path.read_text(encoding="utf-8").splitlines() if line]
    flagged = [t for t in texts if lexicon.asks_for_human(plain(t)) or lexicon.out_of_scope_topic(plain(t))]
    assert len(texts) > 500 and flagged == []
