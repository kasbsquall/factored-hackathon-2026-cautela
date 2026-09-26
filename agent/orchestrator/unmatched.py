"""Transfers made before a single charge is identified, and the bound on clarifying turns.

Bounded clarification. Each turn that ends without a usable candidate counts one clarifying round: a request for
details, a list of options, and a quoted reference that matches none of the customer's records. MAX_CLARIFY_ROUNDS
is 2: the next turn that would ask a third time hands off instead, with the facts gathered so far (how many charges
were searched, the charges shown and rejected, the references not found). Two rounds because the customer has then
given a description and one correction; a third question repeats the second.
"""

from __future__ import annotations

from agent.orchestrator import replies
from agent.orchestrator.steps import StepsMixin, Turn

MAX_CLARIFY_ROUNDS = 2


class UnmatchedMixin(StepsMixin):
    def _transfer_unmatched(self, turn: Turn, confidence: float | None = None) -> replies.Reply:
        """Hand off a conversation where no single charge was identified (see the module docstring)."""
        state = turn.state
        if state.searched is not None:
            state.add_fact(f"Searched {state.searched} own transactions of the last 90 days; no single charge was "
                           "identified", f"list_recent_transactions:{turn.trace_id}")
        return self._escalate(turn, "low_confidence", [], confidence=confidence, questions=self._gathered(turn))

    @staticmethod
    def _gathered(turn: Turn) -> list[str]:
        state = turn.state
        out = []
        if state.rejected:
            out.append("Charges shown and rejected by the customer: "
                       + "; ".join(f"{rid} ({label})" for rid, label in state.rejected))
        if state.missing_refs:
            out.append("References the customer gave that match none of their records: "
                       + ", ".join(state.missing_refs))
        if state.clarify_rounds >= MAX_CLARIFY_ROUNDS:
            out.append(f"Transferred after {state.clarify_rounds} clarifying rounds without a usable candidate "
                       f"(limit {MAX_CLARIFY_ROUNDS}).")
        return out
