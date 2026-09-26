"""Number words and regional amount slang, one test per documented mapping."""

from __future__ import annotations

from datetime import date

import pytest

from ml.features.numbers import IMPLIES_PESOS, SLANG_CURRENCY, SLANG_MULTIPLIERS, words_to_digits
from ml.features.parse import parse_description

REPORT = date(2026, 3, 18)


def amount(text: str) -> tuple[float | None, str | None]:
    p = parse_description(text, REPORT)
    return p.amount, p.currency


@pytest.mark.parametrize("text,value", [
    ("un cargo de cuatrocientos dólares", 400),
    ("me cobraron mil quinientos pesos", 1500),
    ("un cobro de dos millones de pesos", 2_000_000),
    ("cuarenta y cinco mil pesos", 45_000),
    ("doscientos treinta mil pesos", 230_000),
    ("un millón de pesos", 1_000_000),
    ("medio millón de pesos", 500_000),
    ("mil pesos en la tienda", 1_000),
    ("veinticinco dólares", 25),
])
def test_spanish_number_words(text, value):
    assert amount(text)[0] == pytest.approx(value)


@pytest.mark.parametrize("text,value", [
    ("uma cobrança de quinhentos dólares", 500),
    ("uns dois mil pesos", 2_000),
    ("cerca de trezentos e cinquenta dólares", 350),
    ("um milhão de pesos", 1_000_000),
    ("cinquenta conto", 50),
])
def test_portuguese_number_words(text, value):
    assert amount(text)[0] == pytest.approx(value)


@pytest.mark.parametrize("text,value,currency", [
    ("como 20 lucas", 20_000, "PESOS"),          # AR and CO: luca = mil pesos
    ("una luca", 1_000, "PESOS"),
    ("veinte barras", 20_000, "PESOS"),          # CO: barra = mil pesos
    ("como 1,7 palos", 1_700_000, None),         # CO and AR: palo = un millón
    ("dos palos", 2_000_000, None),
    ("medio palo", 500_000, None),
    ("tres gambas", 300, "PESOS"),               # AR: gamba = cien pesos
    ("500 varos", 500, "PESOS"),                 # MX: varo = peso
    ("500 baros", 500, "PESOS"),
    ("cincuenta contos", 50, None),              # BR: conto = one unit of money
])
def test_regional_slang(text, value, currency):
    assert amount(text) == (pytest.approx(value), currency)


def test_every_slang_word_has_a_provenance_and_is_tested():
    for table in (SLANG_MULTIPLIERS, SLANG_CURRENCY):
        for word, (_, source) in table.items():
            assert "ASALE" in source or "team assumption" in source, word
    assert IMPLIES_PESOS <= set(SLANG_MULTIPLIERS)


@pytest.mark.parametrize("text", ["un cargo raro hace dos semanas", "una transferencia hace un mes",
                                  "hace tres días en la app", "um pagamento há duas semanas"])
def test_small_number_words_that_are_not_money_are_left_alone(text):
    assert amount(text)[0] is None


def test_digits_keep_their_scale_word():
    assert words_to_digits("como 36 mil pesos") == "como 36 mil pesos"
    assert amount("de 260 mil en medellin")[0] == 260_000
    assert amount("de 1,9 millones de pesos")[0] == pytest.approx(1_900_000)


def test_relative_dates_still_parse_with_number_words():
    p = parse_description("un cobro de quinientos dólares hace dos semanas", REPORT)
    assert p.amount == 500 and (p.date_lo, p.date_hi) == (date(2026, 2, 28), date(2026, 3, 8))
