"""After a dispute is filed and read back, asking for a person creates a human handoff that carries the filed case,
its verified facts and the actions taken, so the person does not start over. Before this, the agent answered that
the conversation had ended and nothing was handed off. Phrasings were written for these tests."""

from __future__ import annotations

import pytest

from agent.handoff import validate_handoff
from tests.orchestrator.conftest import case_rows, not_recognized


def _filed(orch, case, token, lang="es"):
    first = not_recognized(orch, token, orch.turn(token, case.opener(lang), language=lang))
    done = orch.confirm(token, first.conversation_id, first.confirmation["confirmation_id"])
    assert done.stage == "resolved" and done.case["verified"]
    return done


@pytest.mark.parametrize(("lang", "message"), (("es", "Gracias. Ahora quiero hablar con una persona, por favor"),
                                               ("pt", "Obrigado. Agora quero falar com um atendente")))
def test_a_person_after_filing_gets_the_case_and_its_facts(rig, make_orchestrator, cases, login, lang, message):
    queue: list[dict] = []
    orch = make_orchestrator(sink=lambda state, doc: queue.append(doc))
    case = cases["normal"]
    token = login(case)
    done = _filed(orch, case, token, lang)
    case_id = done.case["case_id"]
    after = orch.turn(token, message, done.conversation_id)
    assert after.stage == "handed_off" and len(queue) == 1 and queue[0] is after.handoff
    handoff = after.handoff
    assert handoff["transfer_reason"]["code"] == "customer_requested_human"
    assert "SYN-HUMAN-001" in handoff["transfer_reason"]["rule_ids"]
    assert handoff["request"]["disputed_transaction_ids"] == [case.transaction_id]
    assert {"action": "open_dispute_case", "status": "verified", "record_id": case_id} in handoff["actions_taken"]
    assert any(f["source"] == f"get_case_status:{case_id}" for f in handoff["verified_facts"])
    assert any(f["source"] == f"get_transaction:{case.transaction_id}" for f in handoff["verified_facts"])
    assert any(case_id in q for q in handoff["open_questions"])
    assert case_id in after.reply
    assert case_rows(rig, case.customer_id) == 1, "nothing is filed again"
    validate_handoff(handoff)


def test_small_talk_after_filing_still_closes(make_orchestrator, cases, login):
    orch = make_orchestrator()
    case = cases["normal"]
    token = login(case)
    done = _filed(orch, case, token)
    after = orch.turn(token, "Muchas gracias por todo", done.conversation_id)
    assert after.stage == "resolved" and after.handoff is None
