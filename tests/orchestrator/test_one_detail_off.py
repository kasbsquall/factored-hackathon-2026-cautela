"""An abstention on a two-cue description that fits a charge on one cue and is one misremembered detail off on the
other (ml/recall_bounds.py, fitted on train) asks with numbered options instead of transferring; "none of these"
still ends in a transfer with nothing written, and a charge the customer rejected is never acted on afterwards.
Phrasings were written for these tests."""

from __future__ import annotations

from datetime import timedelta

from agent.orchestrator.disposition import Disposition
from ml.recall_bounds import AMOUNT_RATIO, DATE_DAYS
from tests.orchestrator.conftest import case_rows, not_recognized, steps


class AlwaysEscalate:
    name = "always_escalate"

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return Disposition("escalate", 0.02, [], self.name)


class Scripted:
    """Returns the scripted decisions in order."""

    name = "scripted"

    def __init__(self, *decisions: Disposition) -> None:
        self.decisions = list(decisions)

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return self.decisions.pop(0)


def _exact_amount_days_off(tx: dict, days: int) -> str:
    when = tx["transaction_date"] - timedelta(days=days)
    return f"No reconozco un cargo de {float(tx['amount']):.2f} {tx['currency']} del {when.day}/{when.month}"


def test_the_bounds_are_the_ones_fitted_on_train():
    assert (DATE_DAYS, AMOUNT_RATIO) == (12, 1.80)  # ml/reports/recall_bounds.json


def test_an_exact_amount_with_the_date_days_off_is_asked_about_not_transferred(rig, make_orchestrator, cases, login):
    case = cases["normal"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    token = login(case)
    first = orch.turn(token, _exact_amount_days_off(case.transaction, 4), language="es")
    assert first.stage == "clarifying", first.reply
    assert next(s for s in first.trail if s.step == "decide.plausible").outcome == "clarify"
    pick = next(o for o in first.options if o.record_id == case.transaction_id)
    shown = orch.turn(token, str(pick.index), first.conversation_id)
    done = orch.confirm(token, shown.conversation_id,
                        not_recognized(orch, token, shown).confirmation["confirmation_id"])
    assert done.stage == "resolved" and case_rows(rig, case.customer_id) == 1


def test_none_of_these_still_transfers_with_nothing_written(rig, make_orchestrator, cases, login):
    case = cases["normal"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    token = login(case)
    last = orch.turn(token, _exact_amount_days_off(case.transaction, 4), language="es")
    while last.stage == "clarifying":
        last = orch.turn(token, "No es ninguno de esos", last.conversation_id)
    assert last.stage == "handed_off" and last.handoff["transfer_reason"]["code"] == "low_confidence"
    assert case_rows(rig, case.customer_id) == 0
    assert "tool.get_dispute_policy" not in steps(last)


def test_a_date_beyond_the_bound_is_not_asked_about(make_orchestrator, cases, login):
    case = cases["normal"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    result = orch.turn(login(case), _exact_amount_days_off(case.transaction, DATE_DAYS + 12), language="es")
    assert case.transaction_id not in [o.record_id for o in result.options]


def test_a_rejected_charge_is_never_acted_on_afterwards(rig, make_orchestrator, cases, login):
    case = cases["normal"]
    tx = case.transaction
    other = "TX-NOT-SHOWN"
    orch = make_orchestrator(disposition=Scripted(
        Disposition("clarify", 0.4, [case.transaction_id], "scripted"),
        Disposition("resolve", 0.95, [case.transaction_id, other], "scripted")))
    token = login(case)
    first = orch.turn(token, f"Me aparece un cargo de {float(tx['amount']):.2f} {tx['currency']} que no hice",
                      language="es")
    assert [o.record_id for o in first.options] == [case.transaction_id]
    after = orch.turn(token, "Ninguno de esos", first.conversation_id)
    assert next(s for s in after.trail if s.step == "decide.rejected").outcome == "escalate"
    assert after.stage == "handed_off" and after.recognition is None
    assert "tool.get_transaction" not in steps(after) and case_rows(rig, case.customer_id) == 0
