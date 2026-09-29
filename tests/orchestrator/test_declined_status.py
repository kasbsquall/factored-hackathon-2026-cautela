"""Regression: a declined charge described in Portuguese ended in a low_confidence handoff while the same charge in
Spanish was explained as not disputable (SYN-STATUS-002).

Cause: the disposition models were trained on labels where only approved or pending debits can be the charge a
customer means, so a declined charge that fits every cue gets a low ranker score, and the outcome then turned on
incidental wording (the Portuguese opener says "compra", which adds a type cue, removes the near-fitting
neighbours and pushes P(no_match) over the abstain threshold). The fix sends a charge that fits every cue and moved
no money straight to the policy engine, whatever disposition model runs.
"""

from __future__ import annotations

from datetime import date

import pytest

from agent.orchestrator.disposition import MODELS_DIR, Disposition, LearnedDisposition, RuleDisposition, \
    non_disputable_fit
from agent.orchestrator import evidence
from tests.orchestrator.conftest import case_rows, steps

LANGS = ("es", "pt")


class AlwaysEscalate:
    """The worst case of the original defect: the model hands off whatever it is shown."""

    name = "always_escalate"

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return Disposition("escalate", 0.0, [], self.name)


def _disposition(kind: str):
    if kind == "learned":
        if not (MODELS_DIR / "systems.pkl").is_file():
            pytest.skip("learned artifacts are git-ignored; train with `uv run python -m ml.train`")
        return LearnedDisposition.load()
    return {"rules": RuleDisposition, "escalating": AlwaysEscalate}[kind]()


@pytest.mark.parametrize("kind", ("rules", "escalating", "learned"))
@pytest.mark.parametrize("lang", LANGS)
def test_declined_charge_is_explained_in_both_languages(rig, make_orchestrator, cases, login, lang, kind):
    case = cases["declined"]
    orch = make_orchestrator(disposition=_disposition(kind))
    result = orch.turn(login(case), case.opener(lang), language=lang)
    assert result.stage == "abstained" and result.handoff is None
    assert "decide.status_check" in steps(result) and "decide.disposition" not in steps(result)
    policy = next(s for s in result.trail if s.step == "decide.policy")
    assert "SYN-STATUS-002" in policy.rule_ids and policy.detail["allowed_writes"] == []
    assert case_rows(rig, case.customer_id) == 0


def test_a_disputable_charge_still_goes_to_the_disposition_model(make_orchestrator, cases, login):
    case = cases["normal"]
    result = make_orchestrator().turn(login(case), case.opener("pt"), language="pt")
    assert "decide.status_check" not in steps(result) and "decide.disposition" in steps(result)


def _tx(tid: str, status: str, amount: float = 500.0, day: str = "2026-05-30", merchant: str = "Farmacia Salud"):
    return {"transaction_id": tid, "transaction_date": f"{day}T10:00:00", "amount": amount, "currency": "MXN",
            "transaction_type": "Purchase", "channel": "POS", "merchant_name": merchant, "transaction_status": status,
            "transaction_city": None}


TEXT = "No reconozco un cargo de 500 MXN en Farmacia Salud el 30 de mayo."
TODAY = date(2026, 5, 31)


def test_fit_requires_one_non_disputable_charge_on_every_cue():
    declined, approved = _tx("T1", "Declined"), _tx("T2", "Approved", amount=90.0, day="2026-05-02")
    assert non_disputable_fit(TEXT, TODAY, {}, [declined, approved])["transaction_id"] == "T1"
    assert non_disputable_fit(TEXT, TODAY, {}, [_tx("T3", "Reversed"), approved])["transaction_id"] == "T3"


def test_fit_leaves_the_decision_to_the_model_otherwise():
    declined = _tx("T1", "Declined")
    assert non_disputable_fit(TEXT, TODAY, {}, [_tx("T2", "Approved")]) is None  # fits, but disputable
    assert non_disputable_fit(TEXT, TODAY, {}, [declined, _tx("T2", "Approved")]) is None  # a retry: two fit
    assert non_disputable_fit("No reconozco un cargo de 500 MXN.", TODAY, {}, [declined]) is None  # one cue only
    assert non_disputable_fit(TEXT, TODAY, {}, [_tx("T4", "Declined", amount=900.0)]) is None  # amount off
    assert non_disputable_fit(TEXT, TODAY, {}, []) is None


# ---- a charge that moved money and matches at least as well is preferred ------------------------------------------
def _w(tid: str, status: str, amount: float, day: str) -> dict:
    return {**_tx(tid, status, amount=amount, day=day, merchant=None), "transaction_type": "Withdrawal",
            "channel": "ATM"}


def test_an_exact_amount_on_another_day_fits_as_well_as_a_declined_charge_near_every_cue():
    text = "Fue un retiro de 44.501 pesos el 31 de marzo."  # amount, date and type
    pool = [_w("D", "Declined", 37892.40, "2026-03-29"), _w("A", "Approved", 44501.30, "2026-03-25"),
            _w("F", "Approved", 60000.0, "2026-02-01")]
    overrides = {"amount": 44501.0, "currency": "PESOS", "date_hint": date(2026, 3, 31), "date_tolerance_days": 0}
    strength = evidence.fit_strengths(text, date(2026, 4, 2), overrides, pool)
    # D: amount 15% off and date 2 days off fit loosely (1 + 1), the type exactly (2); A: the exact amount (2) and
    # the type (2), the date misses; F: only the type
    assert strength == {"D": 4, "A": 4, "F": 2}
    exact = {**overrides, "amount": 37892.40, "date_hint": date(2026, 3, 29)}
    assert evidence.fit_strengths(text, date(2026, 4, 2), exact, pool)["D"] == 6


def test_no_money_moved_is_not_said_while_a_charge_that_moved_money_fits_as_well(rig, make_orchestrator, cases,
                                                                                   login):
    case = cases["declined"]  # a declined purchase; the pool holds an approved purchase 15 days earlier
    orch = make_orchestrator()
    rival = "TX00010274"
    result = orch.turn(login(case), "No reconozco una compra de 18353.53 MXN del 28/5", language="es")
    check = next(s for s in result.trail if s.step == "decide.status_check")
    assert check.outcome == "disputable_rival" and rival in check.detail["rivals"]
    assert result.stage != "abstained" and "decide.disposition" in steps(result)
    if result.stage == "clarifying":
        assert result.options[0].record_id == rival
    assert case_rows(rig, case.customer_id) == 0


def test_a_declined_charge_that_fits_better_is_still_explained(make_orchestrator, cases, login):
    case = cases["declined"]
    result = make_orchestrator().turn(login(case), case.opener("es"), language="es")
    check = next(s for s in result.trail if s.step == "decide.status_check")
    assert check.outcome == "fits_non_disputable_charge" and result.stage == "abstained"
