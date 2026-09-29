"""Understand and route one customer message, at every stage of the conversation.

Order on every turn, whatever the conversation is waiting for:

  1. security screen   injection detector, then another customer's id in the text (security_event, SYN-SEC-001)
  2. understand        parser, or the model merged with the parser (intent.merge)
  3. model flag        a grounded instruction quote from the model is a security event too; it can only add one
  4. person           request_human hands off (a record quoted with it is read first, so another customer's
                       record is still a security event)
  5. references, scope a quoted record goes through the tool layer, which turns another customer's record into a
                       security event; then out_of_scope hands off. Both apply while a recognition, a confirmation
                       or an option list is pending
  6. pending           while a recognition or a confirmation waits, a message that carries a charge cue is a new
                       description: the pending step is withdrawn and the candidate search restarts. Anything else
                       gets the pending question again
  7. options, decide   an option pick acts on that charge; otherwise the description is added and decide runs

A conversation that already ended (resolved, handed off, closed) still runs the security screen and the reference
check: a later attempt to reach another customer's records is recorded and re-escalated as security_event, so a
first-turn low_confidence handoff cannot hide it. After a handoff, a new request (a person, an out-of-scope topic,
a card block, another charge) is appended to that handoff as an open question and the transfer reason is kept
(followup.py). Nothing else runs on a closed conversation.
"""

from __future__ import annotations

import time

from jsonschema import SchemaError

from agent.llm.port import LLMOutputError, LLMUnavailable
from agent.orchestrator import intent as nlu
from agent.orchestrator import lexicon
from agent.orchestrator.followup import FollowUpMixin
from agent.orchestrator.state import Option
from agent.orchestrator.steps import Turn

CHARGE_CUES = frozenset({"amount", "date", "merchant", "type"})


def names_a_charge(cues: set[str], text: str) -> bool:
    """Whether the words name a charge: an amount, a date, a merchant or a transaction type, or a channel ("en un
    cajero", "por la app") said about a charge. A channel alone names a place, not a charge: "¿dónde queda la
    sucursal?" is not a dispute, "un cargo en un cajero que no reconozco" is."""
    if cues & CHARGE_CUES:
        return True
    return "channel" in cues and lexicon.mentions_charge(nlu.plain(text))
READ_FOR_KIND = {"transaction": ("get_transaction", "transaction_id"), "case": ("get_case_status", "case_id")}


class RoutingMixin(FollowUpMixin):
    # ---- understand ----------------------------------------------------------------------------------------
    def _understand(self, turn: Turn, message: str) -> nlu.DisputeIntent:
        started, today, n = time.perf_counter(), self.service.clock().date(), len(turn.state.options)
        kept: list[str] = []
        if n and (choice := nlu.pick_option(message, n)) is not None:
            found = nlu.DisputeIntent(intent="select_option", selected_option=choice, fallback_reason="option_pick")
        elif self.llm is None:
            found = nlu.parse_intent(message, today, n, reason="no_llm_configured")
        else:
            found, kept = self._understand_with_model(turn, message, today, n)
        self._step(turn, "understand", found.source,
                   {"intent": found.intent, "fallback_reason": found.fallback_reason, "topic": found.topic,
                    "amount": found.amount, "currency": found.currency,
                    "date": found.date.isoformat() if found.date else None, "merchant": found.merchant,
                    "selected_option": found.selected_option, "record_ref": found.record_ref,
                    "kept_from_parser": kept, "injection_flag": bool(found.injection)}, started=started)
        return found

    def _understand_with_model(self, turn: Turn, message: str, today, n: int) -> tuple[nlu.DisputeIntent, list[str]]:
        strict = nlu.parse_intent(message, today, n)
        try:
            data = self.llm.extract(message, nlu.intent_schema(today, n), known_names=self._names(turn),
                                    trace_id=turn.trace_id)
            quote, quote_dropped = nlu.injection_quote(data, message)
            found, dropped = nlu.from_llm(data, message, today)
        except (LLMUnavailable, LLMOutputError, SchemaError, ValueError) as exc:
            return nlu.parse_intent(message, today, n, reason=nlu.validation_reason(exc)), []
        dropped += ["injected_instruction"] if quote_dropped else []
        found, kept = nlu.merge(found, strict, message)
        found = found.model_copy(update={"injection": quote})
        if dropped:
            found = found.model_copy(update={"fallback_reason": "dropped_unstated:" + ",".join(dropped)})
        if found.intent == "out_of_scope" and self._detail_in_scope(turn, strict, message):
            found = found.model_copy(update={"intent": "dispute_charge", "topic": None,
                                             "fallback_reason": f"charge_detail_in_scope:{found.topic}"})
        # Escalation signals the parser reads (a person, an out-of-scope topic) apply whatever the model said:
        # they can only make the outcome stricter, like narrow().
        if strict.intent in ("request_human", "out_of_scope") and found.intent != strict.intent:
            found = strict.model_copy(update={"fallback_reason": f"parser_escalation_over_llm:{found.intent}",
                                              "injection": quote})
        return found, kept

    def _detail_in_scope(self, turn: Turn, strict: nlu.DisputeIntent, message: str) -> bool:
        """While the service waits for charge details (it asked, and the conversation is collecting or clarifying),
        a message the parser reads as a dispute and that carries a charge cue answers that question: "fue una
        transferencia de 1700 dólares" describes the disputed charge, it does not ask for a transfer. The model's
        out-of-scope label is then replaced by the parser's own reading, so the model never leads to more than the
        parser alone would; an explicit request the parser reads ("quiero transferir 500") still escalates, and the
        first message of a conversation keeps the model's label."""
        state = turn.state
        if state.stage not in ("collecting", "clarifying") or state.clarify_rounds == 0:
            return False
        if strict.intent != "dispute_charge":
            return False
        return strict.has_charge_cues() or self._new_description(strict, message)

    # ---- security screen -----------------------------------------------------------------------------------
    def _screen(self, turn: Turn, message: str) -> bool:
        """Deterministic security checks that need no tool call. True when the turn was handed off."""
        marker = nlu.detect_injection(message)
        if marker:
            self._step(turn, "understand", "injection_detector", {"marker": marker})
            self._security(turn, "prompt_injection", marker)
            return True
        foreign = [r for r in nlu.find_refs(message)
                   if nlu.ref_kind(r) == "customer" and r != turn.session.customer_id]
        if foreign:
            self._security(turn, "cross_customer_reference", "customer id of another customer in the message")
            return True
        return False

    def _foreign_reference(self, turn: Turn, refs: list[str]) -> bool:
        """Read each quoted transaction or case through the tool layer; another customer's record is a security
        event (the guard flags it). True when the turn was handed off."""
        for ref in refs:
            read = READ_FOR_KIND.get(nlu.ref_kind(ref))
            if read is None:
                continue
            result = self._tool(turn, read[0], {read[1]: ref})
            if not result.ok and result.handoff_reason == "security_event":
                self._security(turn, "cross_customer_access", f"{result.tool}:{result.error.code}")
                return True
        return False

    def _closed_turn(self, turn: Turn, message: str) -> None:
        refs = [r for r in nlu.find_refs(message) if nlu.ref_kind(r) != "customer"]
        if self._screen(turn, message) or self._foreign_reference(turn, refs):
            return
        if turn.state.stage == "handed_off" and turn.state.handoff is not None and message:
            self._follow_up(turn, message)
            return
        self._say(turn, "closed")

    # ---- route ---------------------------------------------------------------------------------------------
    def _route(self, turn: Turn, message: str) -> None:
        state = turn.state
        if self._screen(turn, message):
            return
        found = self._understand(turn, message)
        if found.injection:
            self._security(turn, "prompt_injection", "model flag: " + found.injection[:48])
            return
        refs = [r for r in dict.fromkeys([*(filter(None, [found.record_ref])), *nlu.find_refs(message)])
                if nlu.ref_kind(r) != "customer"]
        if found.intent == "request_human":
            if not (refs and self._foreign_reference(turn, refs)):
                self._escalate(turn, "customer_requested_human", self._policy_only(human=True).rule_ids)
            return
        if refs:  # before the out-of-scope route: a model may label "es la transacción TX..." out of scope
            self._supersede(turn, "reference")
            self._from_reference(turn, refs[0], found)
            return
        if found.intent == "out_of_scope":
            decision = self._policy_only(intent=found.topic or "other_customer_request")
            self._escalate(turn, "out_of_scope", decision.rule_ids, topic=found.topic)
            return
        if found.intent == "block_card":
            self._supersede(turn, "block_card")
            self._block_flow(turn, None)
            return
        if state.pending is not None or state.recognition is not None:
            if not (found.intent == "dispute_charge" and self._new_description(found, message)):
                self._ask_pending(turn)
                return
            self._supersede(turn, "new_description")
        if state.options and found.intent == "select_option" and found.selected_option:
            if found.selected_option <= len(state.options):
                self._select(turn, state.options[found.selected_option - 1])
                return
        if state.options and found.intent == "reject_options":
            state.rejected += [(o.record_id, o.label) for o in state.options
                               if o.kind == "transaction" and (o.record_id, o.label) not in state.rejected]
            state.options = []
        state.statements.append(message)
        state.slots.merge(found)
        self._decide(turn)

    def _new_description(self, found: nlu.DisputeIntent, message: str) -> bool:
        return found.has_charge_cues() or names_a_charge(nlu.text_cues(message, self.service.clock().date()), message)

    def _ask_pending(self, turn: Turn) -> None:
        state = turn.state
        if state.pending is not None:
            self._say(turn, "pending_confirmation", {"label": state.pending.label}, (state.pending.label,))
            return
        label = state.recognition.label
        self._say(turn, "pending_recognition", {"label": label}, (label,))

    def _supersede(self, turn: Turn, why: str) -> None:
        """Withdraw a pending recognition, confirmation or option list that a new message replaced. The withdrawn
        confirmation can no longer be used: confirm() requires the pending one."""
        state = turn.state
        if state.pending is None and state.recognition is None and not state.options:
            return
        self._step(turn, "route.superseded", why,
                   {"recognition": state.recognition.transaction_id if state.recognition else None,
                    "confirmation": state.pending.tool if state.pending else None, "options": len(state.options)})
        state.pending = state.recognition = None
        state.options = []

    def _from_reference(self, turn: Turn, ref: str, found: nlu.DisputeIntent) -> None:
        kind = nlu.ref_kind(ref)
        if kind == "product" or found.intent == "block_card":
            self._block_flow(turn, ref if kind == "product" else None)
            return
        if kind == "case":
            result = self._tool(turn, "get_case_status", {"case_id": ref})
            if not result.ok:
                self._reference_problem(turn, result)
                return
            turn.state.add_fact(f"Case {ref} has status {result.data['status']}", f"get_case_status:{ref}")
            self._escalate(turn, "customer_requested_human", self._policy_only(human=True).rule_ids,
                           questions=[f"Customer asked about case {ref} (status {result.data['status']})."])
            return
        turn.state.statements.append(f"[reference {ref}]")
        self._act_on_transaction(turn, ref, None, "customer_reference")

    def _select(self, turn: Turn, option: Option) -> None:
        self._step(turn, "decide.selection", "customer_selected", {"option": option.index, "kind": option.kind})
        turn.state.options = []
        if option.kind == "card":
            self._request_confirmation(turn, "block_card", {"product_id": option.record_id,
                                                            "reason": "customer_request"}, option.label, None, None)
            return
        self._act_on_transaction(turn, option.record_id, None, "customer_selection")
