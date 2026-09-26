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
