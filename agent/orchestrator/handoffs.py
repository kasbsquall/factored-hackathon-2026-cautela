"""Escalate step: assemble the structured handoff from what the conversation verified.

Facts come only from tool reads recorded in the state (each with the tool and record it came from), actions carry
the status the verify step established, and the transfer reason and rule ids come from the policy engine or the
security layer. The builder in agent/handoff.py masks free text and validates against
docs/schemas/handoff.schema.json; an invalid document raises instead of reaching the console.

Free text in the handoff is English (the console is an internal tool); `language` tells the agent which language
to answer the customer in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.handoff import ActionTaken, Fact, build_handoff, validate_handoff
from agent.orchestrator.state import ConversationState
from agent.security.pii import mask_text

MAX_FOLLOW_UPS = 5  # open questions a customer can add after the transfer; repeats are not added
FOLLOW_UP_PREFIX = "After the transfer the customer "

OPEN_QUESTIONS = {
    "amount_above_threshold": "Review the disputed charge; its USD amount is at or above the review threshold.",
    "suspected_fraud": "Assess the fraud signal on the charge and decide whether the card should be blocked.",
    "policy_requires_review": "Decide how to proceed; policy could not approve automatic handling (see rule ids).",
    "low_confidence": "Identify which charge the customer means; the service could not match one with confidence.",
    "tool_failure": "Check the case system before contacting the customer; a write or read could not be verified.",
    "security_event": "Verify the customer's identity and intent; the conversation tried to reach records or "
                      "instructions outside this customer's scope.",
    "out_of_scope": "Attend a request that is outside dispute intake.",
    "customer_requested_human": "The customer asked to talk to a person.",
}
SUMMARY = {
    "security_event": "Conversation stopped by a security control before any action.",
    "out_of_scope": "Customer request outside dispute intake ({topic}).",
    "customer_requested_human": "Customer asked for a person during dispute intake.",
}


def _cues(state: ConversationState) -> str:
    s = state.slots
    parts = []
    if s.amount is not None:
        parts.append(f"amount about {s.amount:.2f}{' ' + s.currency if s.currency else ''}")
    if s.date is not None:
        parts.append(f"date around {s.date.isoformat()}")
    if s.merchant:
        parts.append(f"merchant '{s.merchant}'")
    return ", ".join(parts)


def summary(state: ConversationState, reason_code: str, topic: str | None) -> str:
    if reason_code in SUMMARY:
        return SUMMARY[reason_code].format(topic=topic or "unspecified")
    text = "Customer does not recognize a charge"
    if cues := _cues(state):
        text += f" ({cues})"
    if state.transaction_id:
        text += f"; identified transaction {state.transaction_id}"
    if state.statements:
        text += f". Customer statement: {state.statements[0][:200]}"
    return text


def assemble(state: ConversationState, *, trace_id: str, reason_code: str, rule_ids: list[str], now: datetime,
             known_names: tuple[str, ...] = (), topic: str | None = None, confidence: float | None = None,
             extra_questions: list[str] | None = None) -> dict[str, Any]:
    questions = [OPEN_QUESTIONS.get(reason_code, OPEN_QUESTIONS["policy_requires_review"]), *(extra_questions or [])]
    if state.options and not state.transaction_id:
        questions.append("Candidate charges shown to the customer: "
                         + "; ".join(f"{o.record_id} ({o.label})" for o in state.options))
    return build_handoff(
        trace_id=trace_id, language=state.language, customer_ref=state.customer_ref,
        summary=summary(state, reason_code, topic), intent="unrecognized_charge" if not topic else topic,
        reason_code=reason_code, rule_ids=rule_ids, created_at=now,
        verified_facts=[Fact(f, s) for f, s in state.facts],
        actions_taken=[ActionTaken(a, st, rid) for a, st, rid in state.actions],
        open_questions=questions, evidence=list(state.evidence),
        disputed_transaction_ids=[state.transaction_id] if state.transaction_id else [],
        confidence=None if confidence is None else max(0.0, min(1.0, confidence)), known_names=known_names,
    )


def append_question(document: dict[str, Any], question: str, known_names: tuple[str, ...] = ()) -> bool:
    """Add an open question to a handoff already sent, keeping its transfer reason and everything else.

    The new document is validated against the schema before it replaces the question list. The dict itself is kept
    (not copied) because the console queue holds this same object. False when the question is already there or the
    customer already added MAX_FOLLOW_UPS of them."""
    masked = mask_text(question, list(known_names))
    questions = list(document["open_questions"])
    added_before = sum(q.startswith(FOLLOW_UP_PREFIX) for q in questions)
    if masked in questions or added_before >= MAX_FOLLOW_UPS:
        return False
    updated = [*questions, masked]
    validate_handoff({**document, "open_questions": updated})
    document["open_questions"] = updated
    return True
