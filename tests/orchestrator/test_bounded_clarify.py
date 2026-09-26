"""Bounded clarification: turns without a usable candidate stop at MAX_CLARIFY_ROUNDS and hand off with what was
gathered (agent/orchestrator/unmatched.py). Phrasings were written for these tests."""

from __future__ import annotations

from agent.orchestrator.disposition import Disposition
from agent.orchestrator.unmatched import MAX_CLARIFY_ROUNDS


class AlwaysClarify:
    """A disposition model that always asks the customer to pick among the first three charges of the pool."""

    name = "always_clarify"

    def decide(self, text, report_date, overrides, pool) -> Disposition:
        return Disposition("clarify", 0.3, [t["transaction_id"] for t in pool[:3]], self.name)


def test_unknown_references_are_bounded_and_listed_for_the_agent(make_orchestrator, cases, login):
    orch = make_orchestrator()
    token = login(cases["normal"])
    refs = ["TX98765432", "TX87654321", "TX76543210"]
    result = orch.turn(token, f"Quiero reclamar la operación {refs[0]}", language="es")
    for ref in refs[1:]:
        assert result.stage == "collecting"
        result = orch.turn(token, f"Perdón, era la {ref}", result.conversation_id)
    assert result.stage == "handed_off"
    handoff = result.handoff
    assert handoff["transfer_reason"]["code"] == "low_confidence"
    listed = next(q for q in handoff["open_questions"] if q.startswith("References the customer gave"))
    assert all(r in listed for r in refs)
    assert any(f"limit {MAX_CLARIFY_ROUNDS}" in q for q in handoff["open_questions"])


def test_rejected_options_end_in_a_handoff_that_lists_them(make_orchestrator, cases, login):
    orch = make_orchestrator(disposition=AlwaysClarify())
    token = login(cases["normal"])
    first = orch.turn(token, "Hay una compra de la semana pasada que no me suena", language="es")
    assert first.stage == "clarifying" and first.options
    shown = [o.record_id for o in first.options]
    second = orch.turn(token, "No, ninguno de esos", first.conversation_id)
    assert second.stage == "clarifying" and second.options
    third = orch.turn(token, "Tampoco, ninguno", first.conversation_id)
    assert third.stage == "handed_off" and third.handoff["transfer_reason"]["code"] == "low_confidence"
    rejected = next(q for q in third.handoff["open_questions"] if q.startswith("Charges shown and rejected"))
    assert all(rejected.count(tid) == 1 for tid in shown), "options shown twice are listed once"
    assert any(f["fact"].startswith("Searched ") for f in third.handoff["verified_facts"])


def test_vague_answers_stop_after_two_questions(make_orchestrator, cases, login):
    orch = make_orchestrator()
    token = login(cases["normal"])
    result = orch.turn(token, "Tengo un problema con un movimiento", language="es")
    asked = 0
    while result.stage in ("clarifying", "collecting"):
        asked += 1
        result = orch.turn(token, "La verdad no me acuerdo", result.conversation_id)
    assert asked == MAX_CLARIFY_ROUNDS and result.stage == "handed_off"
    assert result.handoff["transfer_reason"]["code"] == "low_confidence"
