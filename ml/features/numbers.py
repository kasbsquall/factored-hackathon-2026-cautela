"""Spanish and Portuguese number words and regional money slang.

``words_to_digits`` rewrites number words as digits ("mil quinientos" -> "1500",
"dos millones" -> "2000000", "medio palo" -> "0.5 palo") so the amount regex in
parse.py can read them. A number-word sequence is rewritten only when its value
is at least 100 or it is followed by a currency or slang money word, so phrases
such as "dos semanas" or "un cargo" are left alone.

Slang meanings are listed in ``SLANG_MULTIPLIERS`` and ``SLANG_CURRENCY`` with
their provenance (see ml/README.md, "Amount slang"). Most are team assumptions.
"""

from __future__ import annotations

import re

_UNITS_ES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21,
    "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
    "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600,
    "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900,
    "novecientas": 900,
}
_UNITS_PT = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15,
    "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
    "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100,
    "duzentos": 200, "duzentas": 200, "trezentos": 300, "trezentas": 300, "quatrocentos": 400,
    "quatrocentas": 400, "quinhentos": 500, "quinhentas": 500, "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700, "oitocentos": 800, "oitocentas": 800, "novecentos": 900,
    "novecentas": 900,
}
SMALL = {**_UNITS_ES, **_UNITS_PT}
THOUSAND = {"mil"}
MILLION = {"millon", "millones", "milhao", "milhoes"}
CONNECTORS = {"y", "e"}
ARTICLES = {"un", "una", "uno", "um", "uma"}

# slang word -> (multiplier, provenance). The number before the word is multiplied ("20 lucas" -> 20000).
SLANG_MULTIPLIERS: dict[str, tuple[int, str]] = {
    "luca": (1_000, "ASALE Diccionario de americanismos, entry 'luca' (AR, CO, UY: mil pesos)"),
    "lucas": (1_000, "ASALE Diccionario de americanismos, entry 'luca' (AR, CO, UY: mil pesos)"),
    "barra": (1_000, "team assumption (CO colloquial: mil pesos)"),
    "barras": (1_000, "team assumption (CO colloquial: mil pesos)"),
    "palo": (1_000_000, "team assumption (CO and AR colloquial: un millon)"),
    "palos": (1_000_000, "team assumption (CO and AR colloquial: un millon)"),
    "gamba": (100, "team assumption (AR colloquial: cien pesos)"),
    "gambas": (100, "team assumption (AR colloquial: cien pesos)"),
}
# slang word -> (currency, provenance). A unit of money, not a multiplier ("500 varos" -> 500 pesos).
SLANG_CURRENCY: dict[str, tuple[str | None, str]] = {
    "varo": ("PESOS", "team assumption (MX colloquial: peso)"),
    "varos": ("PESOS", "team assumption (MX colloquial: pesos)"),
    "baro": ("PESOS", "team assumption (MX spelling variant of varo)"),
    "baros": ("PESOS", "team assumption (MX spelling variant of varos)"),
    "conto": (None, "team assumption (BR colloquial: one unit of money, 'cinquenta conto' = 50)"),
    "contos": (None, "team assumption (BR colloquial: one unit of money)"),
}
MONEY_WORDS = ({"pesos", "peso", "dolares", "dolar", "usd", "dlls", "dls", "reais"} | set(SLANG_MULTIPLIERS)
               | set(SLANG_CURRENCY) | THOUSAND | MILLION)
MIN_BARE_VALUE = 100


def _value(tokens: list[str]) -> float | None:
    total, current = 0.0, 0.0
    seen_number = False
    for i, w in enumerate(tokens):
        if w in SMALL:
            current += SMALL[w]
            seen_number = True
        elif w in THOUSAND:
            current = (current or 1) * 1_000
            seen_number = True
        elif w in MILLION:
            total += (current or 1) * 1_000_000
            current = 0.0
            seen_number = True
        elif w == "medio" and i + 1 < len(tokens):
            current += 0.5
        elif w in CONNECTORS:
            continue
        else:
            return None
    return (total + current) if seen_number or current else None


def _is_number_word(w: str) -> bool:
    return w in SMALL or w in THOUSAND or w in MILLION or w in CONNECTORS or w == "medio"


def words_to_digits(text: str) -> str:
    """Rewrite number-word runs as digits; ``text`` must already be lowercase without accents."""
    words = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(words):
        if not _is_number_word(words[i]) or words[i] in CONNECTORS:
            out.append(words[i])
            i += 1
            continue
        j = i
        while j < len(words) and _is_number_word(words[j]):
            j += 1
        while j > i and words[j - 1] in CONNECTORS:  # a run never ends on "y" / "e"
            j -= 1
        run = words[i:j]
        if out and out[-1][:1].isdigit() and run[0] in THOUSAND | MILLION:  # "36 mil": digits own the scale
            out.extend(run)
            i = j
            continue
        nxt = re.sub(r"[^a-z]", "", words[j]) if j < len(words) else ""
        value = _value(run)
        standalone_article = len(run) == 1 and run[0] in ARTICLES and nxt not in MONEY_WORDS
        medio_scale = run[-1] == "medio" and nxt in (MILLION | {"palo", "palos", "luca", "lucas"})
        if value is not None and not standalone_article and (value >= MIN_BARE_VALUE or nxt in MONEY_WORDS):
            out.append(str(int(value)) if value == int(value) else str(value))
        elif medio_scale:
            out.append("0.5")
        else:
            out.extend(run)
        i = j
    return " ".join(out)

# slang multipliers that name pesos by themselves ("20 lucas" is 20 mil pesos); "palo" can also mean a million
# dollars in AR usage, so it implies no currency (team assumption)
IMPLIES_PESOS = {"luca", "lucas", "barra", "barras", "gamba", "gambas"}
