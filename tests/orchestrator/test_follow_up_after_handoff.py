"""A new request after the conversation was handed off is appended to that handoff as an open question; the
transfer reason is never replaced. Phrasings were written for these tests."""

from __future__ import annotations

import pytest

from agent.handoff import validate_handoff
from agent.orchestrator import replies
from agent.orchestrator.handoffs import FOLLOW_UP_PREFIX, MAX_FOLLOW_UPS
from tests.orchestrator.conftest import case_rows, not_recognized


def _handed_off_for_amount(rig, orch, case, token, lang="es"):
    first = not_recognized(orch, token, orch.turn(token, case.opener(lang), language=lang))
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.handoff["transfer_reason"]["code"] == "amount_above_threshold"
    return done


@pytest.mark.parametrize(("lang", "message"), (("es", "Ya que estamos, quisiera que me suban el cupo de la tarjeta"),
                                               ("pt", "Aproveitando, queria aumentar o limite do cartão")))
def test_an_out_of_scope_request_after_the_handoff_is_appended(rig, make_orchestrator, cases, login, lang, message):
    queue: list[dict] = []
    orch = make_orchestrator(sink=lambda state, doc: queue.append(doc))
    case = cases["human"]
    token = login(case)
    done = _handed_off_for_amount(rig, orch, case, token, lang)
    before = {k: v for k, v in done.handoff.items() if k != "open_questions"}
    questions_before = list(done.handoff["open_questions"])
    after = orch.turn(token, message, done.conversation_id)
    assert after.stage == "handed_off" and len(queue) == 1, "the same handoff, no second transfer"
    handoff = queue[0]
    assert {k: v for k, v in handoff.items() if k != "open_questions"} == before, "reason and facts unchanged"
    assert handoff["open_questions"][:-1] == questions_before
    assert handoff["open_questions"][-1].startswith(FOLLOW_UP_PREFIX + "raised a request outside dispute intake")
    assert "SYN-SCOPE-001" in handoff["open_questions"][-1]
    validate_handoff(handoff)
    assert replies.FOLLOW_UP[lang]["out_of_scope"] in after.reply


def test_a_repeated_request_is_noted_once_and_notes_are_capped(rig, make_orchestrator, cases, login):
    orch = make_orchestrator()
    case = cases["human"]
    token = login(case)
    done = _handed_off_for_amount(rig, orch, case, token)
    for _ in range(2):
        orch.turn(token, "Por favor, necesito hablar con alguien del banco", done.conversation_id)
    state = orch.store.get(done.conversation_id, case.customer_id)
    notes = [q for q in state.handoff["open_questions"] if q.startswith(FOLLOW_UP_PREFIX)]
    assert len(notes) == 1 and "SYN-HUMAN-001" in notes[0]
    for day in range(1, 10):
        orch.turn(token, f"Otra cosa: tampoco reconozco un cargo de {day}00 USD del {day} de mayo",
                  done.conversation_id)
    notes = [q for q in state.handoff["open_questions"] if q.startswith(FOLLOW_UP_PREFIX)]
    assert len(notes) == MAX_FOLLOW_UPS
    assert state.handoff["transfer_reason"]["code"] == "amount_above_threshold"


def test_another_charge_after_the_handoff_is_described_by_its_cues_only(rig, make_orchestrator, cases, login):
    orch = make_orchestrator()
    case = cases["human"]
    token = login(case)
    done = _handed_off_for_amount(rig, orch, case, token)
    rows = case_rows(rig, case.customer_id)
    message = "Y ojo que además hay otro cobro de 77.50 USD el 12 de mayo que tampoco hice"
    after = orch.turn(token, message, done.conversation_id)
    note = after.handoff["open_questions"][-1]
    assert note.startswith(FOLLOW_UP_PREFIX + "described another charge") and "77.50" in note
    assert message not in str(after.handoff), "no transcript text in the handoff"
    assert case_rows(rig, case.customer_id) == rows, "nothing written for the new charge"


def test_small_talk_after_the_handoff_changes_nothing(rig, make_orchestrator, cases, login):
    orch = make_orchestrator()
    case = cases["human"]
    token = login(case)
    done = _handed_off_for_amount(rig, orch, case, token)
    questions = list(done.handoff["open_questions"])
    after = orch.turn(token, "Muchas gracias por la ayuda", done.conversation_id)
    assert after.reply == replies.render_template("closed", "es", {})
    assert after.handoff["open_questions"] == questions

