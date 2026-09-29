"""Self-repairs: a value the customer takes back and restates. In one message, the value after the repair marker
wins; in a later message, the restated slot replaces the earlier one before the next decision, including right
after a low_confidence transfer, which the correction resumes once. Phrasings were written for these tests."""

from __future__ import annotations

from datetime import date

import pytest

from agent.handoff import validate_handoff
from agent.orchestrator import intent as nlu
from agent.orchestrator.disposition import Disposition
from agent.orchestrator.handoffs import FOLLOW_UP_PREFIX
from tests.orchestrator.conftest import case_rows, not_recognized

TODAY = date(2026, 6, 1)


@pytest.mark.parametrize("message, amount, currency, when", [
    ("Vi un cobro de 65 dólares, no, perdón, de 50,31 dólares que no hice", 50.31, "USD", None),
    ("Me aparece un cargo de 180 mil pesos, mejor dicho, de 128.856 pesos el 26 de mayo", 128856.0, "PESOS",
     date(2026, 5, 26)),
    ("un débito de 40 USD, digo, 45 que no reconozco", 45.0, "USD", None),
    ("Uma cobrança de 300 dólares, ou melhor, 212,40 dólares, que não fiz", 212.40, "USD", None),
    ("tem uma compra de 90 reais, desculpa, me enganei: 75 reais no dia 3 de maio", 75.0, None, date(2026, 5, 3)),
    ("un cargo del 3 de mayo, perdón, del 5 de mayo por 20 dólares", 20.0, "USD", date(2026, 5, 5)),
    ("uma cobrança de 20 dólares no dia 10/5, na verdade no dia 12/5", 20.0, "USD", date(2026, 5, 12)),
])
def test_the_value_after_an_in_message_repair_wins(message, amount, currency, when):
    found = nlu.parse_intent(message, TODAY)
    assert (found.amount, found.currency, found.date) == (amount, currency, when)


@pytest.mark.parametrize("message, amount, when", [
    ("me cobraron 50 dólares, te digo que fue el 3 de mayo", 50.0, date(2026, 5, 3)),  # nothing restated
    ("en realidad no sé qué es este cargo de 30 dólares", 30.0, None),  # no value before the marker
    ("un cargo de 30 dólares del 3 de mayo, perdón por escribir tanto", 30.0, date(2026, 5, 3)),
])
def test_a_marker_without_a_restated_value_changes_nothing(message, amount, when):
    found = nlu.parse_intent(message, TODAY)
    assert (found.amount, found.date) == (amount, when)


def test_the_model_reading_of_a_repair_is_grounded_on_the_repaired_value():
    message = "Vi un cobro de 65 dólares, no, perdón, de 50,31 dólares que no hice"
    kept, dropped = nlu.from_llm({"intent": "dispute_charge", "amount": 50.31, "currency": "USD"}, message, TODAY)
    assert kept.amount == 50.31 and "amount" not in dropped
    _, dropped = nlu.from_llm({"intent": "dispute_charge", "amount": 65.0, "currency": "USD"}, message, TODAY)
    assert "amount" in dropped


def _amount_only(tx: dict, amount: float) -> str:
    return f"un cargo de {amount:.2f} {tx['currency']}"  # one cue: no charge can be shown for it alone


def _full(tx: dict) -> str:
    when = tx["transaction_date"]
    return (f"un cargo de {float(tx['amount']):.2f} {tx['currency']} en {tx['merchant_name']} "
            f"del {when.day}/{when.month}")


class Recording:
    """Decides like the given model and records the structured values each decision was given."""

    name = "recording"

    def __init__(self, then) -> None:
        self.then, self.seen = then, []

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        self.seen.append(dict(overrides))
        return self.then.decide(text, report_date, overrides, pool)


def test_a_later_message_replaces_the_slot_before_the_next_decision(make_orchestrator, cases, login):
    from agent.orchestrator import RuleDisposition
    case = cases["normal"]
    tx = case.transaction
    model = Recording(RuleDisposition())
    orch = make_orchestrator(disposition=model)
    token = login(case)
    first = orch.turn(token, f"Me aparece un cargo de {float(tx['amount']) * 4:.2f} {tx['currency']} en "
                             f"{tx['merchant_name']} y no fui yo", language="es")
    orch.turn(token, f"Me equivoqué con el monto: eran {float(tx['amount']):.2f} {tx['currency']}",
              first.conversation_id)
    state = orch.store.get(first.conversation_id, case.customer_id)
    assert state.slots.amount == pytest.approx(float(tx["amount"]))
    assert model.seen[0]["amount"] == pytest.approx(float(tx["amount"]) * 4)
    assert model.seen[-1]["amount"] == pytest.approx(float(tx["amount"])), "decided on the corrected amount"


class EscalateOnce:
    """Abstains on its first decision (a transfer), then decides like the given model."""

    name = "escalate_once"

    def __init__(self, then) -> None:
        self.then, self.calls = then, 0

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        self.calls += 1
        if self.calls == 1:
            return Disposition("escalate", 0.02, [], self.name)
        return self.then.decide(text, report_date, overrides, pool)


class AlwaysEscalate:
    name = "always_escalate"

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return Disposition("escalate", 0.02, [], self.name)


def _transferred_then_corrected(make_orchestrator, login, case, queue):
    from agent.orchestrator import RuleDisposition
    orch = make_orchestrator(disposition=EscalateOnce(RuleDisposition()), sink=lambda state, doc: queue.append(doc))
    token = login(case)
    tx = case.transaction
    wrong = orch.turn(token, "No reconozco " + _amount_only(tx, float(tx["amount"]) * 10), language="es")
    assert wrong.stage == "handed_off" and wrong.handoff["transfer_reason"]["code"] == "low_confidence"
    fixed = orch.turn(token, "Perdón, me equivoqué: fue " + _full(tx), wrong.conversation_id)
    return orch, token, fixed


def test_a_correction_after_a_low_confidence_transfer_resumes_the_search(rig, make_orchestrator, cases, login):
    queue: list[dict] = []
    case = cases["normal"]
    orch, token, fixed = _transferred_then_corrected(make_orchestrator, login, case, queue)
    assert "handoff.resumed" in [s.step for s in fixed.trail]
    shown = not_recognized(orch, token, fixed)
    assert shown.confirmation["charge"]["amount"] == pytest.approx(float(case.transaction["amount"]))
    done = orch.confirm(token, shown.conversation_id, shown.confirmation["confirmation_id"])
    assert done.stage == "resolved" and case_rows(rig, case.customer_id) == 1
    assert len(queue) == 1, "no second transfer"
    notes = [q for q in queue[0]["open_questions"] if q.startswith(FOLLOW_UP_PREFIX)]
    assert "resumed the search" in notes[0] and done.case["case_id"] in notes[-1]
    validate_handoff(queue[0])


def test_a_resumed_search_that_ends_in_a_transfer_updates_the_queued_handoff(make_orchestrator, cases, login):
    queue: list[dict] = []
    case = cases["human"]  # the charge is above the review threshold: a case, then a transfer
    orch, token, fixed = _transferred_then_corrected(make_orchestrator, login, case, queue)
    first_id = queue[0]["handoff_id"]
    shown = not_recognized(orch, token, fixed)
    done = orch.confirm(token, shown.conversation_id, shown.confirmation["confirmation_id"])
    assert done.stage == "handed_off" and len(queue) == 1
    assert queue[0] is done.handoff and queue[0]["handoff_id"] == first_id
    assert queue[0]["transfer_reason"]["code"] == "amount_above_threshold"
    assert queue[0]["request"]["disputed_transaction_ids"] == [case.transaction_id]
    validate_handoff(queue[0])


def test_the_search_resumes_only_once_and_only_on_a_correction(make_orchestrator, cases, login):
    queue: list[dict] = []
    case = cases["normal"]
    orch = make_orchestrator(disposition=AlwaysEscalate(), sink=lambda state, doc: queue.append(doc))
    token = login(case)
    tx = case.transaction
    amount = float(tx["amount"])
    wrong = orch.turn(token, "No reconozco " + _amount_only(tx, amount * 10), language="es")
    same = orch.turn(token, "Repito: " + _amount_only(tx, amount * 10), wrong.conversation_id)
    assert same.stage == "handed_off" and "handoff.resumed" not in [s.step for s in same.trail]
    again = orch.turn(token, "Perdón, era " + _amount_only(tx, amount * 20), wrong.conversation_id)
    assert "handoff.resumed" in [s.step for s in again.trail] and again.stage == "handed_off"
    assert len(queue) == 1 and queue[0] is again.handoff, "the queued handoff is updated, not duplicated"
    last = orch.turn(token, "Perdón, era " + _amount_only(tx, amount * 30), wrong.conversation_id)
    assert "handoff.resumed" not in [s.step for s in last.trail] and last.stage == "handed_off"
    validate_handoff(queue[0])
