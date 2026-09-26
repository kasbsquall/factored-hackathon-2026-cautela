"""A new request after the conversation was handed off: note it on the handoff, keep the transfer reason.

The first transfer reason is what a person must review (an amount above the threshold, a charge the service could
not identify), so a later topic never replaces it. What the customer asks after the transfer (a person, an
out-of-scope topic, a card block, another charge) is appended to that handoff as an open question, in English and
masked, and the customer is told it was added. Another charge is described by the cues the parser read (amount,
date, merchant), never by the customer's words, so the handoff stays free of transcript text. Nothing is searched
or written for the new request. The security screen runs before this (routing._closed_turn), so another customer's
record or an injection still becomes a new security_event handoff.
"""

from __future__ import annotations

from agent.orchestrator import handoffs, replies
from agent.orchestrator import intent as nlu
from agent.orchestrator.actions import ActionsMixin
from agent.orchestrator.steps import Turn

PREFIX = handoffs.FOLLOW_UP_PREFIX


def _cue_text(found: nlu.DisputeIntent) -> str:
    parts = []
    if found.amount is not None:
        parts.append(f"amount about {found.amount:.2f}{' ' + found.currency if found.currency else ''}")
    if found.date is not None:
        parts.append(f"date around {found.date.isoformat()}")
    if found.merchant:
        parts.append(f"merchant '{found.merchant}'")
    return ", ".join(parts) or "no amount, date or merchant read"


class FollowUpMixin(ActionsMixin):
    def _follow_up(self, turn: Turn, message: str) -> None:
        found = self._understand(turn, message)
        if found.injection:
            self._security(turn, "prompt_injection", "model flag: " + found.injection[:48])
            return
        note = self._follow_up_note(found, message)
        if note is None:
            self._say(turn, "closed")
            return
        kind, question, rule_ids = note
        state = turn.state
        added = handoffs.append_question(state.handoff, question, self._names(turn))
        state.add_rules(rule_ids)
        self._step(turn, "handoff.follow_up", kind if added else "not_added",
                   {"handoff_id": state.handoff["handoff_id"],
                    "transfer_reason": state.handoff["transfer_reason"]["code"],
                    "open_questions": len(state.handoff["open_questions"])}, rule_ids)
        request = replies.FOLLOW_UP[state.language][kind]
        self._say(turn, "handoff_follow_up", {"request": request}, (request,))

    def _follow_up_note(self, found: nlu.DisputeIntent, message: str) -> tuple[str, str, list[str]] | None:
        if found.intent == "request_human":
            decision = self._policy_only(human=True)
            return ("customer_requested_human", PREFIX + "asked again to talk to a person "
                    f"({', '.join(decision.rule_ids)}).", decision.rule_ids)
        if found.intent == "out_of_scope":
            topic = found.topic or "other_customer_request"
            decision = self._policy_only(intent=topic)
            return ("out_of_scope", PREFIX + f"raised a request outside dispute intake (topic {topic}, "
                    f"{', '.join(decision.rule_ids)}); attend it as well.", decision.rule_ids)
        if found.intent == "block_card":
            return "block_card", PREFIX + "asked to block a card; no card was blocked in this conversation.", []
        if found.intent == "dispute_charge" and self._new_description(found, message):
            return ("new_charge", PREFIX + f"described another charge ({_cue_text(found)}); nothing was searched "
                    "or written for it.", [])
        return None
