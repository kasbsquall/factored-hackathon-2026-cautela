"""Amounts and dates as the customer reads them, in the same convention as the app's charge cards.

The convention (one per language, documented in agent/README.md, "Amounts and dates"):

  * Amounts: ISO code, a space, the number. Spanish uses the number format of the currency's country: COP and ARS
    as in Colombia and Argentina ("COP 48.900", "ARS 1.234,50"); MXN, USD and every other code as in Mexico and
    Latin American Spanish ("MXN 5,335.32", "USD 980.10"). Portuguese uses the Brazilian format for every code
    ("MXN 5.335,32"). COP has no decimals; every other code has two.
  * Dates: Spanish "30 may 2026", Portuguese "30 de mai. de 2026" (the short month names of es-419 and pt-BR).

The app does the same with Intl.NumberFormat and Intl.DateTimeFormat (app/src/lib/format/index.ts). Both sides are
tested against the same vectors in docs/format-vectors.json, so a reply bubble and a card never disagree.
"""

from __future__ import annotations

import re
from datetime import date, datetime

ZERO_DECIMALS = frozenset({"COP", "CLP", "PYG"})
ES_COMMA_DECIMAL = frozenset({"COP", "ARS"})  # the Spanish-speaking countries of these codes write 1.234,56
ES_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic")
PT_MONTHS = ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez.")
_LABEL_AMOUNT = re.compile(r"^(-?\d+(?:\.\d+)?)\s+([A-Z]{3})$")
_LABEL_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _separators(currency: str, lang: str) -> tuple[str, str]:
    """(thousands, decimal) for a currency in a language."""
    if lang == "pt" or currency in ES_COMMA_DECIMAL:
        return ".", ","
    return ",", "."


def money(amount: float | None, currency: str | None, lang: str) -> str:
    """"MXN 5,335.32" (es), "MXN 5.335,32" (pt). An unknown amount or currency gives an empty string."""
    if amount is None or not currency:
        return ""
    digits = 0 if currency in ZERO_DECIMALS else 2
    group, decimal = _separators(currency, lang)
    text = f"{abs(float(amount)):,.{digits}f}"  # "5,335.32" in Python's own separators
    whole, _, cents = text.partition(".")
    number = whole.replace(",", group) + (decimal + cents if cents else "")
    sign = "-" if float(amount) < 0 and float(text.replace(",", "")) != 0 else ""
    return f"{sign}{currency} {number}"


def date_text(value: date | datetime | str | None, lang: str) -> str:
    """"30 may 2026" (es), "30 de mai. de 2026" (pt). Only the calendar day is used, never a time zone."""
    if value is None or value == "":
        return ""
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if lang == "pt":
        return f"{day.day} de {PT_MONTHS[day.month - 1]} de {day.year}"
    return f"{day.day} {ES_MONTHS[day.month - 1]} {day.year}"


def label_text(label: str, lang: str) -> str:
    """A charge label of steps.tx_label ("30/05/2026, Marketplace Uno, 5335.32 MXN") as the customer reads it:
    "30 may 2026, Marketplace Uno, MXN 5,335.32". Any other text (a card label, a case id) is returned unchanged.

    The machine form stays in the API fields (options, recognition, confirmation), which the app parses and formats
    itself; only reply text uses this form.
    """
    parts = label.split(", ")
    if len(parts) < 3:
        return label
    when, amount = _LABEL_DATE.match(parts[0]), _LABEL_AMOUNT.match(parts[-1])
    if not when or not amount:
        return label
    day = date(int(when.group(3)), int(when.group(2)), int(when.group(1)))
    who = ", ".join(parts[1:-1])
    return f"{date_text(day, lang)}, {who}, {money(float(amount.group(1)), amount.group(2), lang)}"
