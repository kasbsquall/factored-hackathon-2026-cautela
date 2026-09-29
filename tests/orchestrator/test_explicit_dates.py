"""Explicit dates in the deterministic parser: month abbreviations (es, pt, with or without a dot) and numeric
dd/mm, dd/mm/yyyy dates. "4 de feb" used to be read as the 4th of the current month. Phrasings were written for
these tests."""

from __future__ import annotations

from datetime import date

import pytest

from agent.orchestrator import intent as nlu
from ml.features.parse import parse_description

TODAY = date(2026, 3, 10)


@pytest.mark.parametrize("message, expected", [
    ("No reconozco un cargo el 4 de feb, yo no lo hice", date(2026, 2, 4)),
    ("un cobro del 4 feb. que no es mío", date(2026, 2, 4)),
    ("me llegó un débito el 12 de ene", date(2026, 1, 12)),
    ("vi un retiro el 28 dic que no hice", date(2025, 12, 28)),
    ("apareceu uma cobrança no dia 7 de fev que não fiz", date(2026, 2, 7)),
    ("uma compra de 12 set que eu não reconheço", date(2025, 9, 12)),  # September 2026 is after today
    ("foi no dia 3 out.", date(2025, 10, 3)),
    ("uma cobrança de 20 dez 2025", date(2025, 12, 20)),
    ("un cargo el 5 de sept de 2025", date(2025, 9, 5)),
    ("un cargo el 9 de setiembre", date(2025, 9, 9)),
    ("un retiro el 3/2 que no reconozco", date(2026, 2, 3)),
    ("el 03/02/2026 hubo un cobro raro", date(2026, 2, 3)),
    ("cobrança do dia 15/12/2025", date(2025, 12, 15)),
    ("un cargo el 27 de mayo", date(2025, 5, 27)),  # full month names still read
])
def test_explicit_dates_are_read_with_zero_tolerance(message, expected):
    found = nlu.parse_intent(message, TODAY)
    assert (found.date, found.date_tolerance_days) == (expected, 0)
    assert "date" in nlu.text_cues(message, TODAY)


@pytest.mark.parametrize("message", [
    "un cargo el 31/02 que no reconozco",  # no such day
    "un pago del 5/13",  # no month 13
    "otros 12 cargos",  # 'otros' is not 'out'
    "me cobraron 3 mayores",  # 'mayores' is not 'may'
])
def test_words_and_invalid_numbers_are_not_read_as_explicit_dates(message):
    assert nlu.date_spans(nlu.plain(message), TODAY) == []


def test_the_day_of_a_date_is_never_read_as_the_amount():
    for message in ("un cargo del 4 de feb que no hice", "un retiro el 3/2", "una compra del 20 dez"):
        assert nlu.parse_intent(message, TODAY).amount is None, message
        assert "amount" not in nlu.text_cues(message, TODAY), message
        assert parse_description(nlu.cue_text(message, TODAY), TODAY).amount is None, message


def test_an_amount_after_a_date_is_not_taken_for_its_year():
    found = nlu.parse_intent("un cargo del 4 de feb de 2000 pesos", TODAY)
    assert (found.amount, found.currency, found.date) == (2000.0, "PESOS", date(2026, 2, 4))
    found = nlu.parse_intent("un cargo del 4 de feb de 2025 por 30 dólares", TODAY)
    assert (found.amount, found.date) == (30.0, date(2025, 2, 4))


def test_the_ranker_text_keeps_no_date_number_or_month_word():
    text = nlu.cue_text("me cobraron 50 dólares el 4 de mayo en la tienda", TODAY)
    assert text == "me cobraron 50 dolares el [fecha] en la tienda"
    assert parse_description(text, TODAY).amount == 50.0
