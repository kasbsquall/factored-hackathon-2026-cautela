"""Reply text formats amounts and dates exactly like the app's cards (vectors shared with app/tests/unit)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.orchestrator import fmt

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "docs" / "format-vectors.json").read_text("utf-8"))


@pytest.mark.parametrize("case", VECTORS["money"], ids=lambda c: f"{c['currency']}-{c['lang']}-{c['amount']}")
def test_money_matches_the_shared_vectors(case):
    assert fmt.money(case["amount"], case["currency"], case["lang"]) == case["text"]


@pytest.mark.parametrize("case", VECTORS["date"], ids=lambda c: f"{c['value']}-{c['lang']}")
def test_dates_match_the_shared_vectors(case):
    assert fmt.date_text(case["value"], case["lang"]) == case["text"]


@pytest.mark.parametrize("case", VECTORS["label"], ids=lambda c: f"{c['lang']}-{c['label'][:12]}")
def test_charge_labels_match_the_shared_vectors(case):
    assert fmt.label_text(case["label"], case["lang"]) == case["text"]


def test_unknown_amounts_and_dates_are_empty():
    assert fmt.money(None, "USD", "es") == "" and fmt.money(10.0, None, "pt") == ""
    assert fmt.date_text(None, "es") == "" and fmt.date_text("", "pt") == ""


def test_a_datetime_keeps_its_calendar_day():
    assert fmt.date_text("2026-06-18T05:59:41", "es") == "18 jun 2026"
