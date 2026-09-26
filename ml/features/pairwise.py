"""Description-candidate features.

Each candidate transaction gets a fixed-length vector that compares it with the
parsed description. The same vectors feed the rule baseline (hand weights) and
the learned model (fitted weights), so the comparison isolates what learning
adds over hand-set rules.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from difflib import SequenceMatcher
from functools import lru_cache

from ml.features.lexicon import MERCHANT_DIRECTORY
from ml.features.parse import ParsedDescription, norm

DEBIT_TYPES = {"Purchase", "Withdrawal", "Transfer", "Payment", "Adjustment"}
MERCHANT_STOP = {"de", "el", "la", "tv"}
LOGDIFF_CAP = 3.0
DATE_CAP = 60.0

FEATURE_NAMES = [
    "amt_given", "amt_logdiff", "amt_within_25pct", "cur_given", "cur_match",
    "date_given", "date_dist", "date_in_range", "age_norm",
    "merch_max", "merch_mean", "noun_given", "noun_match",
    "type_given", "type_match", "chan_given", "chan_match", "city_match",
    "is_debit", "is_declined", "is_reversed",
    "amt_rank", "date_rank", "merch_rank", "pool_size", "text_merch_signal",
    "merch_idf", "merch_stated", "merch_mismatch",
]
STATED_MIN = 0.85  # IDF-weighted similarity at which a directory merchant counts as named in the text


def candidate_date(c: dict) -> date:
    v = c.get("date") or c["transaction_date"]
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)).date()


@lru_cache(maxsize=4096)
def _token_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def merchant_similarity(text_tokens: tuple[str, ...], merchant: str | None) -> tuple[float, float]:
    """(max, mean) over merchant tokens of the best fuzzy match to any description token."""
    if not merchant:
        return 0.0, 0.0
    toks = _merchant_tokens(merchant)
    bests = [max((_token_sim(t, w) for w in text_tokens), default=0.0) for t in toks]
    return max(bests), sum(bests) / len(bests)


def _merchant_tokens(merchant: str) -> list[str]:
    return [t for t in norm(merchant).split() if len(t) >= 4 and t not in MERCHANT_STOP] or norm(merchant).split()


_DF: dict[str, int] = {}
for _name in MERCHANT_DIRECTORY:
    for _tok in set(_merchant_tokens(_name)):
        _DF[_tok] = _DF.get(_tok, 0) + 1


def merchant_idf_similarity(text_tokens: tuple[str, ...], merchant: str | None) -> float:
    """Token similarity weighted by 1 / (number of directory merchants sharing the token).

    A generic token such as "central" (in two merchants) counts half, so "Laboratorio Central" and
    "Mercado Central" no longer tie on it.
    """
    if not merchant:
        return 0.0
    toks = _merchant_tokens(merchant)
    weights = [1.0 / _DF.get(t, 1) for t in toks]
    sims = [max((_token_sim(t, w) for w in text_tokens), default=0.0) for t in toks]
    return sum(w * s for w, s in zip(weights, sims)) / sum(weights)


def merchant_score(mmax: float, idf: float) -> float:
    """Best-token match, with a small IDF term that breaks ties between merchants sharing a generic word."""
    return mmax * (0.9 + 0.1 * idf)


@lru_cache(maxsize=4096)
def stated_merchants(text_tokens: tuple[str, ...]) -> frozenset[str]:
    """Directory merchants the description names (IDF-weighted similarity >= STATED_MIN)."""
    return frozenset(m for m in MERCHANT_DIRECTORY if merchant_idf_similarity(text_tokens, m) >= STATED_MIN)


def _currency_match(p: ParsedDescription, cur: str | None) -> float:
    if p.currency is None:
        return 0.0
    if p.currency == "PESOS":
        return float(cur in ("COP", "ARS", "MXN"))
    return float(p.currency == cur)


def _ranks(values: list[float]) -> list[float]:
    """Share of other candidates strictly better (smaller); ties share a rank, so input order never matters."""
    n = len(values)
    return [sum(v < x for v in values) / max(1, n - 1) for x in values]


def candidate_features(p: ParsedDescription, cands: list[dict], report: date) -> list[list[float]]:
    text_tokens = tuple(w.strip(".,;:?!¿¡") for w in p.text.split() if len(w) >= 3)
    rows, logdiffs, dists, merchs, extra = [], [], [], [], []
    stated = stated_merchants(text_tokens)
    idf_sims = [merchant_idf_similarity(text_tokens, c.get("merchant_name")) for c in cands]
    for c in cands:
        d = candidate_date(c)
        amt = float(c["amount"])
        if p.amount and amt > 0:
            logdiff = min(abs(math.log(amt / p.amount)), LOGDIFF_CAP)
        else:
            logdiff = LOGDIFF_CAP if p.amount else 0.0
        if p.date_lo is not None:
            dist = float((p.date_lo - d).days if d < p.date_lo else max(0, (d - p.date_hi).days))
            dist = min(dist, DATE_CAP)
        else:
            dist = 0.0
        mmax, mmean = merchant_similarity(text_tokens, c.get("merchant_name"))
        idf = idf_sims[len(rows)]
        city = norm(c.get("transaction_city") or "")
        rows.append([
            float(p.amount is not None), logdiff, float(p.amount is not None and logdiff <= math.log(1.25)),
            float(p.currency is not None), _currency_match(p, c.get("currency")),
            float(p.date_lo is not None), dist, float(p.date_lo is not None and dist == 0),
            (report - d).days / 90.0,
            mmax, mmean, float(bool(p.noun_merchants)), float(c.get("merchant_name") in p.noun_merchants),
            float(bool(p.types)), float(c["transaction_type"] in p.types),
            float(bool(p.channels)), float(c["channel"] in p.channels),
            float(bool(city) and f" {city} " in f" {p.text} "),
            float(c["transaction_type"] in DEBIT_TYPES), float(c.get("transaction_status") == "Declined"),
            float(c.get("transaction_status") == "Reversed"),
        ])
        extra.append([idf, float(bool(stated)),
                      float(bool(stated) and c.get("merchant_name") not in stated and idf < STATED_MIN)])
        logdiffs.append(logdiff)
        dists.append(dist)
        merchs.append(-merchant_score(mmax, idf))
    amt_r, date_r, merch_r = _ranks(logdiffs), _ranks(dists), _ranks(merchs)
    signal = max([max((-m for m in merchs), default=0.0), 1.0 if stated else 0.0])
    for i, row in enumerate(rows):
        row += [amt_r[i], date_r[i], merch_r[i], len(cands) / 10.0, signal, *extra[i]]
    return rows
