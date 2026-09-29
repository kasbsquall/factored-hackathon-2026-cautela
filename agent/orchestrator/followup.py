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


def corrected_slots(state, found: nlu.DisputeIntent) -> list[str]:
    """The slots (amount, date, merchant) a later message restates with a different value than the one held."""
    return [name for name in ("amount", "date", "merchant")
            if getattr(found, name) is not None and getattr(state.slots, name) is not None
            and getattr(found, name) != getattr(state.slots, name)]


class FollowUpMixin(ActionsMixin):
    def _follow_up(self, turn: Turn, message: str) -> None:
        found = self._understand(turn, message)
        if found.injection:
            self._security(turn, "prompt_injection", "model flag: " + found.injection[:48])
            return
        if self._resume_after_correction(turn, found, message):
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

    def _resume_after_correction(self, turn: Turn, found: nlu.DisputeIntent, message: str) -> bool:
        """A correction of a detail right after a low_confidence transfer resumes the search, once.

        The transfer said no charge could be identified from the details given; a message that restates one of
        those details with another value ("the date was the 25th, not the 19th") changes what the search reads, so
        the corrected slot replaces the old one and decide runs again. Only when nothing was identified, written or
        flagged, and only once per conversation. The queued handoff gets a note now; if the search ends in another
        transfer, that same queued handoff is updated in place (steps._escalate), and if it ends any other way,
        a closing note is added (core._finish)."""
        state = turn.state
        if (state.resumed or state.transaction_id or state.actions or state.case_id or found.intent != "dispute_charge"
                or state.handoff["transfer_reason"]["code"] != "low_confidence"):
            return False
        fields = corrected_slots(state, found)
        if not fields:
            return False
        handoffs.append_question(state.handoff, PREFIX + f"corrected the {' and '.join(fields)} ({_cue_text(found)}); "
                                 "the service resumed the search for the charge.", self._names(turn))
        self._step(turn, "handoff.resumed", "correction", {"handoff_id": state.handoff["handoff_id"],
                                                           "corrected": fields})
        state.reopened, state.handoff, state.resumed, state.stage = state.handoff, None, True, "collecting"
        state.statements.append(message)
        state.slots.merge(found)
        self._decide(turn)
        return True

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
