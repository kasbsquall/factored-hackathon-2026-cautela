"""Ranker interface, shaped like ``agent.tools.ranking.CandidateRanker``.

The protocol is duplicated here so ``ml/`` does not import the agent package at
training time. A ranker has a ``name`` and ``rank(features, candidates)`` that
returns ``(transaction_id, score in [0, 1])`` for every candidate, best first.

``features`` may be either

* a mapping with ``text`` and ``report_date`` (what the offline evaluation passes), or
* the agent's ``DescriptionFeatures`` object (attributes ``text``, ``amount``,
  ``currency``, ``date_hint``, ``date_tolerance_days``, ``merchant_hint``).

Structured values supplied by the caller override what the parser reads from
the text. Relative dates need a reference day: ``report_date`` when given,
otherwise ``date_hint`` when given, otherwise today.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from ml.features.parse import ParsedDescription, norm, parse_description


@runtime_checkable
class CandidateRanker(Protocol):
    name: str

    def rank(self, features: Any, candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        ...


def _get(features: Any, key: str) -> Any:
    if isinstance(features, Mapping):
        return features.get(key)
    return getattr(features, key, None)


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def coerce_features(features: Any) -> tuple[ParsedDescription, date]:
    """Parse the free text and apply any structured overrides."""
    date_hint = _as_date(_get(features, "date_hint"))
    report = _as_date(_get(features, "report_date")) or date_hint or date.today()
    text = _get(features, "text") or ""
    merchant_hint = _get(features, "merchant_hint")
    if merchant_hint:
        text = f"{text} {merchant_hint}"
    p = parse_description(text, report)
    if _get(features, "amount") is not None:
        p.amount = float(_get(features, "amount"))
    if _get(features, "currency"):
        p.currency = str(_get(features, "currency")).upper()
    if date_hint is not None:
        tol = int(_get(features, "date_tolerance_days") or 0)
        p.date_lo, p.date_hi = date_hint - timedelta(days=tol), date_hint + timedelta(days=tol)
    p.text = norm(text)
    return p, report


def order(ids: Sequence[str], scores: Sequence[float], candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """Best first; ties broken by recency, then id, so the order never depends on input order."""
    when = {c["transaction_id"]: str(c.get("transaction_date") or c.get("ts") or "") for c in candidates}
    items = [(i, float(round(s, 6))) for i, s in zip(ids, scores)]
    return sorted(items, key=lambda it: (-it[1], _neg(when[it[0]]), it[0]))


def _neg(s: str) -> str:
    return "".join(chr(0x10FFFF - ord(c)) for c in s)
