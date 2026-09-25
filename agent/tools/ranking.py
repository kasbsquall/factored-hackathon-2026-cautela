"""Candidate ranking for find_candidate_charges, behind a small pluggable interface.

The complaints table has no transaction_id, so the charge a customer disputes has to be inferred from what they
say (amount, date, merchant). A CandidateRanker scores the customer's own transactions against those hints. The
default is deterministic and rule-based; learned rankers from ml/ can replace it without touching the tool, the
permission layer or the ambiguity rule, which stays here so every ranker is judged the same way.

Ambiguity rule (documented in the tool output as `ambiguity_rule`):
  a match is ambiguous when there is no candidate, when the top score is below AMBIGUITY_MIN_TOP, or when the top
  score beats the second by less than AMBIGUITY_MIN_MARGIN. An ambiguous result never names a best candidate;
  the service asks the customer a clarifying question instead of guessing.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Protocol, runtime_checkable

AMBIGUITY_MIN_TOP = 0.60
AMBIGUITY_MIN_MARGIN = 0.15
AMBIGUITY_RULE = (f"ambiguous if no candidate, top score < {AMBIGUITY_MIN_TOP}, "
                  f"or top minus second < {AMBIGUITY_MIN_MARGIN}")

AMOUNT_TOLERANCE = 0.10  # relative difference at which the amount score reaches zero
WEIGHTS = {"amount": 0.5, "date": 0.3, "merchant": 0.2}


@dataclass(frozen=True)
class DescriptionFeatures:
    """Structured hints extracted from the customer's description. `text` is the masked free text, if any."""

    amount: float | None = None
    currency: str | None = None
    date_hint: date | None = None
    date_tolerance_days: int = 3
    merchant_hint: str | None = None
    text: str | None = None


@runtime_checkable
class CandidateRanker(Protocol):
    name: str

    def rank(self, features: DescriptionFeatures,
             candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        """Return (transaction_id, score in [0, 1]) for every candidate, best first."""
        ...


def is_ambiguous(scores: Sequence[float]) -> bool:
    ordered = sorted(scores, reverse=True)
    if not ordered or ordered[0] < AMBIGUITY_MIN_TOP:
        return True
    return len(ordered) > 1 and ordered[0] - ordered[1] < AMBIGUITY_MIN_MARGIN


def _plain(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.casefold()).split())


def amount_score(hint: float, currency: str | None, tx: Mapping[str, Any]) -> float:
    if tx.get("amount") is None or (currency and tx.get("currency") and currency != tx["currency"]):
        return 0.0
    actual = float(tx["amount"])
    rel = abs(actual - hint) / max(abs(actual), 0.01)
    return max(0.0, 1.0 - rel / AMOUNT_TOLERANCE)


def date_score(hint: date, tolerance: int, tx: Mapping[str, Any]) -> float:
    when = tx.get("transaction_date")
    if when is None:
        return 0.0
    day = when.date() if isinstance(when, datetime) else when
    return math.exp(-abs((day - hint).days) / max(tolerance, 1))


def merchant_score(hint: str, tx: Mapping[str, Any]) -> float:
    name = tx.get("merchant_name")
    if not name:
        return 0.0
    a, b = _plain(hint), _plain(name)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = len(tokens_a & tokens_b) / len(tokens_a)
    return max(overlap, SequenceMatcher(None, a, b).ratio())


class RuleBasedRanker:
    """Weighted proximity on amount (0.5), date (0.3) and merchant (0.2), renormalized over the hints given."""

    name = "rule_based_v1"

    def components(self, f: DescriptionFeatures, tx: Mapping[str, Any]) -> dict[str, float]:
        parts: dict[str, float] = {}
        if f.amount is not None:
            parts["amount"] = amount_score(f.amount, f.currency, tx)
        if f.date_hint is not None:
            parts["date"] = date_score(f.date_hint, f.date_tolerance_days, tx)
        if f.merchant_hint:
            parts["merchant"] = merchant_score(f.merchant_hint, tx)
        return parts

    def score(self, f: DescriptionFeatures, tx: Mapping[str, Any]) -> float:
        parts = self.components(f, tx)
        total = sum(WEIGHTS[k] for k in parts)
        return round(sum(WEIGHTS[k] * v for k, v in parts.items()) / total, 4) if total else 0.0

    def rank(self, features: DescriptionFeatures,
             candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
        scored = [(tx["transaction_id"], self.score(features, tx)) for tx in candidates]
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    def explain(self, features: DescriptionFeatures, tx: Mapping[str, Any]) -> list[str]:
        return [f"{name}={value:.2f}" for name, value in self.components(features, tx).items()]
