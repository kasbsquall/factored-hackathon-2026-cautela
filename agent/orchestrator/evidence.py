"""What the customer sees about a charge: its verified data, and why it matched what they said.

Two things the customer needs before disputing, both built only from tool reads and the ranker's own features,
never from model text:

* `charge_details` turns a TransactionView (get_transaction, list_recent_transactions) and the card list of
  get_customer_profile into the fields a candidate card and the "do you recognize it?" step show: date, amount and
  currency, merchant name, category (transaction category and MCC code), channel, city, the card's last 4 digits
  (already masked by the profile tool), type and status.
* `match_reasons` recomputes the pairwise features of `ml.features.pairwise` for the same description and pool the
  disposition model read, and turns the cue tests that fired (the same tests and tolerances as the case features
  in `ml.disposition`) into reason codes with a short Spanish or Portuguese label. A reason appears only when its
  feature fired for that charge: no reason is inferred, and a charge with none shows none.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from agent.orchestrator.disposition import ranker_input
from ml.disposition import IDX, _cue_hits, cue_fits
from ml.features.pairwise import candidate_date, candidate_features
from ml.rankers.protocol import coerce_features

CARD_TYPES = frozenset({"Credit Card", "Debit Card"})
EXACT_AMOUNT_PCT = 0.5  # below half a percent the amount is reported as the same amount
DAY_TOLERANCE = 1  # a date hint this tight is a day the customer named; wider hints are periods ("last week")

LABELS: dict[str, dict[str, str]] = {
    "es": {
        "amount_exact": "Mismo monto que indicaste",
        "amount_close": "Monto a {n}% del que indicaste",
        "date_same_day": "El mismo día que indicaste",
        "date_within_days": "A {n} {days} de la fecha que indicaste",
        "date_in_range": "Dentro del período que mencionaste",
        "date_near": "A {n} {days} del período que mencionaste",
        "merchant_named": "Comercio que mencionaste",
        "type_match": "Tipo de operación que mencionaste",
        "channel_match": "Canal que mencionaste",
        "city_match": "Ciudad que mencionaste",
        "only_fit": "Único cargo de los últimos {n} días que coincide con todo lo que dijiste",
        "customer_selected": "Lo elegiste de la lista",
        "customer_reference": "Referencia que escribiste",
    },
    "pt": {
        "amount_exact": "Mesmo valor que você informou",
        "amount_close": "Valor a {n}% do que você informou",
        "date_same_day": "No mesmo dia que você informou",
        "date_within_days": "A {n} {days} da data que você informou",
        "date_in_range": "Dentro do período que você mencionou",
        "date_near": "A {n} {days} do período que você mencionou",
        "merchant_named": "Loja que você mencionou",
        "type_match": "Tipo de operação que você mencionou",
        "channel_match": "Canal que você mencionou",
        "city_match": "Cidade que você mencionou",
        "only_fit": "Única cobrança dos últimos {n} dias que bate com tudo o que você disse",
        "customer_selected": "Você escolheu na lista",
        "customer_reference": "Referência que você escreveu",
    },
}
DAYS = {"es": ("día", "días"), "pt": ("dia", "dias")}
# The pairwise feature behind each reason code, recorded in the decision trail so the audit shows what fired.
FEATURE_OF = {
    "amount_exact": "amt_within_25pct", "amount_close": "amt_within_25pct", "date_same_day": "date_dist",
    "date_within_days": "date_dist", "date_in_range": "date_in_range", "date_near": "date_dist",
    "merchant_named": "merch_max", "type_match": "type_match", "channel_match": "chan_match",
    "city_match": "city_match", "only_fit": "cue_fits",
}


def reason(code: str, lang: str, value: int | None = None) -> dict[str, Any]:
    template = LABELS[lang][code]
    one, many = DAYS[lang]
    label = template.format(n=value, days=one if value == 1 else many) if value is not None else template
    return {"code": code, "label": label, "value": value}


def card_digits(products: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """product_id -> card type and last 4 digits, from the already masked get_customer_profile products."""
    out: dict[str, dict[str, str]] = {}
    for p in products:
        digits = "".join(ch for ch in str(p.get("product_number_masked") or "") if ch.isdigit())[-4:]
        if p.get("product_type") in CARD_TYPES and len(digits) == 4:
            out[str(p["product_id"])] = {"card_type": str(p["product_type"]), "card_last4": digits}
    return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (datetime, date)) else str(value)


def charge_details(tx: Mapping[str, Any], cards: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """The verified fields of one charge. Every value is a tool field; missing ones stay None."""
    card = (cards or {}).get(str(tx.get("product_id") or ""), {})
    amount = tx.get("amount")
    return {
        "transaction_date": _iso(tx.get("transaction_date")),
        "amount": None if amount is None else float(amount),
        "currency": tx.get("currency"),
        "merchant_name": tx.get("merchant_name"),
        "merchant_category": tx.get("merchant_category"),
        "category": tx.get("transaction_category"),
        "channel": tx.get("channel"),
        "city": tx.get("transaction_city"),
        "country": tx.get("transaction_country"),
        "card_type": card.get("card_type"),
        "card_last4": card.get("card_last4"),
        "transaction_type": tx.get("transaction_type"),
        "transaction_status": tx.get("transaction_status"),
    }


def _date_reason(row: list[float], parsed: Any, overrides: Mapping[str, Any], tx: Mapping[str, Any],
                 lang: str) -> dict[str, Any] | None:
    hint = overrides.get("date_hint")
    if isinstance(hint, date) and int(overrides.get("date_tolerance_days") or 0) <= DAY_TOLERANCE:
        # the customer gave a day: say how far the charge is from it
        days = abs((candidate_date(dict(tx)) - hint).days)
        return reason("date_same_day", lang) if days == 0 else reason("date_within_days", lang, days)
    # a period ("last week"): the distance to the period, as the date feature measures it
    dist = int(row[IDX["date_dist"]])
    if dist == 0:
        same = parsed.date_lo is not None and parsed.date_lo == parsed.date_hi
        return reason("date_same_day" if same else "date_in_range", lang)
    return reason("date_near", lang, dist)


def _row_reasons(row: list[float], parsed: Any, overrides: Mapping[str, Any], tx: Mapping[str, Any],
                 lang: str) -> list[dict[str, Any]]:
    hits = _cue_hits(row)
    out: list[dict[str, Any]] = []
    if hits.get("amount") and parsed.amount and tx.get("amount") is not None:
        pct = abs(float(tx["amount"]) - parsed.amount) / parsed.amount * 100
        out.append(reason("amount_exact", lang) if pct < EXACT_AMOUNT_PCT
                   else reason("amount_close", lang, max(1, round(pct))))
    if hits.get("date"):
        found = _date_reason(row, parsed, overrides, tx, lang)
        if found:
            out.append(found)
    if hits.get("merchant"):
        out.append(reason("merchant_named", lang))
    if hits.get("type"):
        out.append(reason("type_match", lang))
    if hits.get("channel"):
        out.append(reason("channel_match", lang))
    if row[IDX["city_match"]]:
        out.append(reason("city_match", lang))
    return out


def match_reasons(text: str, report_date: date, overrides: Mapping[str, Any], pool: Sequence[Mapping[str, Any]],
                  ids: Sequence[str], lang: str, window_days: int) -> dict[str, list[dict[str, Any]]]:
    """Reasons for each id in `ids`, from the features computed over the whole pool (ranks depend on it)."""
    candidates = [dict(t) for t in pool if t.get("amount") is not None]
    if not candidates:
        return {i: [] for i in ids}
    inp = ranker_input(text, report_date, overrides)
    parsed, report = coerce_features(inp)
    rows = dict(zip((c["transaction_id"] for c in candidates), candidate_features(parsed, candidates, report)))
    cues, fits = cue_fits(inp, candidates)
    by_id = {c["transaction_id"]: c for c in candidates}
    out: dict[str, list[dict[str, Any]]] = {}
    for tid in ids:
        if tid not in rows:
            out[tid] = []
            continue
        found = _row_reasons(rows[tid], parsed, overrides, by_id[tid], lang)
        if cues and fits == [tid]:
            found.append(reason("only_fit", lang, window_days))
        out[tid] = found
    return out
