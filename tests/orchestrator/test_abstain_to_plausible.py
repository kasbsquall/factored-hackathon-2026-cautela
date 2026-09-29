"""An abstention while some charge fits the description on every cue, or on all but one of three or more, shows
those charges instead of transferring (Orchestrator._plausible_instead). A charge the policy sends to a person is
then transferred with its own reason and charge id. Phrasings were written for these tests."""

from __future__ import annotations

from datetime import date

from agent.handoff import validate_handoff
from agent.orchestrator.disposition import Disposition, plausible_charges
from tests.orchestrator.conftest import not_recognized, steps

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


class AlwaysEscalate:
    """A disposition model that abstains on every ranking."""

    name = "always_escalate"

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return Disposition("escalate", 0.12, [], self.name)


def own_words(tx: dict, amount: float | None = None) -> str:
    when = tx["transaction_date"]
    value = float(tx["amount"]) if amount is None else amount
    return (f"Veo un débito de {value:.2f} {tx['currency']} en {tx['merchant_name']} del {when.day} de "
            f"{MONTHS[when.month - 1]} que yo no hice")


def test_a_charge_that_misses_one_cue_is_shown_and_then_handled_by_policy(make_orchestrator, cases, login):
    case = cases["human"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    token = login(case)
    wrong_amount = float(case.transaction["amount"]) * 3  # amount in another currency: one cue of three missed
    first = orch.turn(token, own_words(case.transaction, wrong_amount), language="es")
    assert first.stage == "clarifying", first.reply
    assert next(s for s in first.trail if s.step == "decide.plausible").outcome == "clarify"
    pick = next(o for o in first.options if o.record_id == case.transaction_id)
    shown = orch.turn(token, str(pick.index), first.conversation_id)
    done = orch.confirm(token, shown.conversation_id,
                        not_recognized(orch, token, shown).confirmation["confirmation_id"])
    handoff = done.handoff
    assert handoff["transfer_reason"]["code"] == "amount_above_threshold"
    assert handoff["request"]["disputed_transaction_ids"] == [case.transaction_id]
    validate_handoff(handoff)


def test_a_description_nothing_fits_is_transferred_without_asking(make_orchestrator, cases, login):
    orch = make_orchestrator(disposition=AlwaysEscalate())
    result = orch.turn(login(cases["human"]), "Me aparece un cobro de 98765.43 USD en Joyería Imaginaria del 2 de "
                                              "enero y no fui yo", language="es")
    assert result.stage == "handed_off" and result.handoff["transfer_reason"]["code"] == "low_confidence"
    assert next(s for s in result.trail if s.step == "decide.plausible").outcome == "none"


def test_one_cue_is_too_little_to_show_a_charge(make_orchestrator, cases, login):
    case = cases["human"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    amount = f"{float(case.transaction['amount']):.2f}"
    result = orch.turn(login(case), f"Me salió un cobro de {amount} y ni idea qué es", language="es")
    assert result.stage == "handed_off" and result.handoff["transfer_reason"]["code"] == "low_confidence"
    assert not result.options


def test_rejected_charges_are_not_shown_again(make_orchestrator, cases, login):
    case = cases["human"]
    orch = make_orchestrator(disposition=AlwaysEscalate())
    token = login(case)
    first = orch.turn(token, own_words(case.transaction), language="es")
    shown = [o.record_id for o in first.options]
    assert first.stage == "clarifying" and case.transaction_id in shown
    second = orch.turn(token, "No, ninguno de esos", first.conversation_id)
    assert not {o.record_id for o in second.options} & set(shown), "a rejected charge is never shown again"
    last = second
    while last.stage == "clarifying":
        last = orch.turn(token, "Tampoco, no es ninguno", first.conversation_id)
    assert last.stage == "handed_off" and last.handoff["transfer_reason"]["code"] == "low_confidence"
    assert "tool.get_dispute_policy" not in steps(last)
    rejected = next(q for q in last.handoff["open_questions"] if q.startswith("Charges shown and rejected"))
    assert all(tid in rejected for tid in shown)


def charge(tid: str, day: int, amount: float, kind: str, channel: str) -> dict:
    return {"transaction_id": tid, "transaction_date": f"2026-03-{day:02d} 10:00:00", "amount": amount,
            "currency": "MXN", "transaction_type": kind, "channel": channel, "merchant_name": None,
            "merchant_category": None, "transaction_city": "Monterrey", "transaction_country": "Mexico",
            "transaction_status": "Approved"}


POOL = [charge("TRX-A", 11, 7096.71, "Transfer", "Web"), charge("TRX-B", 12, 4480.00, "Payment", "App"),
        charge("TRX-C", 20, 4510.00, "Transfer", "POS"), charge("TRX-D", 11, 4495.00, "Transfer", "ATM"),
        charge("TRX-E", 2, 380.00, "Withdrawal", "ATM")]


def test_plausible_charges_allow_one_missed_cue_when_three_are_read():
    three_cues = "una transferencia de 4,500 el 11 de marzo que no reconozco"  # amount, date, type
    assert plausible_charges(three_cues, date(2026, 4, 2), {}, POOL) == ["TRX-D", "TRX-A", "TRX-B", "TRX-C"]
    one_cue = "un cobro de 4,500 que no reconozco"
    assert plausible_charges(one_cue, date(2026, 4, 2), {}, POOL) == []


def test_with_two_cues_one_may_be_off_by_one_misremembered_detail_only():
    # amount and type: C and D fit both; A is a transfer whose amount is 1.58 times the stated one (within the 1.8
    # fitted on train, ml/recall_bounds.py); B fits the amount but is a payment, and a type has no "near"; E is a
    # withdrawal 11.8 times off
    two_cues = "una transferencia de 4,500 que no reconozco"
    assert plausible_charges(two_cues, date(2026, 4, 2), {}, POOL) == ["TRX-C", "TRX-D", "TRX-A"]
    # amount and date: B (the exact amount) and D fit the amount and are 8 and 7 days after the stated day, within
    # 12; C fits the amount 16 days after; A misses both; E is 2 days off but 11.8 times the amount
    exact_amount = "un cobro de 4,480 el 4 de marzo que no reconozco"
    assert plausible_charges(exact_amount, date(2026, 4, 2), {}, POOL) == ["TRX-B", "TRX-D"]
