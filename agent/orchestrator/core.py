"""The request lifecycle: session gate -> understand -> decide -> act -> verify -> escalate.

    orchestrator = Orchestrator(service, llm=None | MaskedLLM, disposition=load_default())
    result = orchestrator.turn(session_token, "No reconozco un cargo de 350 pesos del 27 de mayo")
    result = orchestrator.confirm(session_token, result.conversation_id, result.confirmation["confirmation_id"])

The model interprets (extract) and phrases (reply). Everything else is code: the session gate, ownership and
confirmation checks (ToolService), the policy decision (rules.yaml), the verify read-back and the handoff. Each
step appends to the decision trail and writes an audit record under the turn's trace id, with rule ids, tool
calls, latency, and the LLM tokens and cost of the turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import SchemaError

from agent.llm.port import LanguageModel, LLMOutputError, LLMUnavailable
from agent.orchestrator import intent as nlu
from agent.orchestrator import replies
from agent.orchestrator.actions import ActionsMixin
from agent.orchestrator.disposition import DispositionModel, load_default
from agent.orchestrator.state import FINAL_STAGES, ConversationStore, Option, TrailStep
from agent.orchestrator.steps import HandoffSink, Turn, tx_label
from agent.security.audit import new_trace_id
from agent.security.session import AuthError
from agent.service import ToolService
from ml.features.pairwise import merchant_similarity

MAX_MESSAGE_CHARS = 2000
MAX_CLARIFY_ROUNDS = 2
POOL_ARGS = {"window_days": 90, "limit": 50}
MERCHANT_MENTION_MIN = 0.88  # 0.8 let 'conta' match 'Conecta' (0.83) in a pt run


@dataclass
class TurnResult:
    conversation_id: str | None
    trace_id: str
    language: str
    stage: str
    reply: str
    reply_source: str
    options: list[Option] = field(default_factory=list)
    confirmation: dict[str, Any] | None = None
    case: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    trail: list[TrailStep] = field(default_factory=list)
    error: str | None = None
    llm: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


class Orchestrator(ActionsMixin):
    def __init__(self, service: ToolService, llm: LanguageModel | None = None,
                 disposition: DispositionModel | None = None, store: ConversationStore | None = None,
                 handoff_sink: HandoffSink | None = None) -> None:
        self.service = service
        self.llm = llm
        self.disposition = disposition or load_default()
        self.store = store or ConversationStore()
        self.handoff_sink = handoff_sink

    # ---- public entry points ---------------------------------------------------------------------------
    def turn(self, session_token: str, message: str, conversation_id: str | None = None,
             language: str | None = None) -> TurnResult:
        trace_id, started = new_trace_id(), time.perf_counter()
        session = self._gate(session_token, trace_id)
        if isinstance(session, TurnResult):
            return session
        message = (message or "").strip()[:MAX_MESSAGE_CHARS]
        if conversation_id:
            state = self.store.get(conversation_id, session.customer_id)
            if state is None:
                return self._error(trace_id, "conversation_not_found", language or "es")
        else:
            lang = language if language in ("es", "pt") else nlu.detect_language(message)
            state = self.store.create(session.customer_id, session.customer_ref, lang, self.service.clock())
        turn = Turn(state, session_token, session, trace_id, started)
        state.trace_ids.append(trace_id)
        state.transcript.append(("customer", message))
        self._step(turn, "gate", "session_valid", {"conversation_id": state.conversation_id})
        if state.stage in FINAL_STAGES:
            self._say(turn, "closed")
        elif not message:
            self._say(turn, "ask_details", {"missing": self._missing_text(state, set())})
        else:
            self._guarded(turn, lambda: self._route(turn, message))
        return self._finish(turn)

    def confirm(self, session_token: str, conversation_id: str, confirmation_id: str,
                accept: bool = True) -> TurnResult:
        trace_id, started = new_trace_id(), time.perf_counter()
        session = self._gate(session_token, trace_id)
        if isinstance(session, TurnResult):
            return session
        state = self.store.get(conversation_id, session.customer_id)
        if state is None:
            return self._error(trace_id, "conversation_not_found", "es")
        pending = state.pending
        if pending is None or pending.confirmation_id != confirmation_id:
            return self._error(trace_id, "no_pending_confirmation", state.language, state.conversation_id)
        turn = Turn(state, session_token, session, trace_id, started)
        state.trace_ids.append(trace_id)
        state.pending = None
        self._step(turn, "confirm.answer", "accepted" if accept else "declined",
                   {"confirmation_id": confirmation_id, "tool": pending.tool})
        if accept:
            self._guarded(turn, lambda: self._execute_confirmed(turn, pending))
        else:
            state.stage = "closed"
            self._say(turn, "declined")
        return self._finish(turn)

    # ---- gate --------------------------------------------------------------------------------------------
    def _gate(self, token: str, trace_id: str):
        try:
            return self.service.identity.validate(token)
        except AuthError as exc:
            self.service.audit.record(trace_id=trace_id, step="orchestrator.gate", outcome="denied", reason=exc.code)
            return self._error(trace_id, exc.code, "es", auth=True)

    def _error(self, trace_id: str, code: str, lang: str, conversation_id: str | None = None,
               auth: bool = False) -> TurnResult:
        if auth:
            why = replies.WHY[lang].get(code, replies.WHY[lang]["default"])
            text = replies.render_template("auth_required", lang, {"why": why})
        else:
            text = ""
        stage = "auth_required" if auth else "error"
        return TurnResult(conversation_id, trace_id, lang, stage, text, "template", error=code)

    # ---- understand ----------------------------------------------------------------------------------------
    def _understand(self, turn: Turn, message: str) -> nlu.DisputeIntent:
        started, today, n = time.perf_counter(), self.service.clock().date(), len(turn.state.options)
        if n and (choice := nlu.pick_option(message, n)) is not None:
            found = nlu.DisputeIntent(intent="select_option", selected_option=choice, fallback_reason="option_pick")
        elif self.llm is None:
            found = nlu.parse_intent(message, today, n, reason="no_llm_configured")
        else:
            try:
                data = self.llm.extract(message, nlu.intent_schema(today, n), known_names=self._names(turn),
                                        trace_id=turn.trace_id)
                found, dropped = nlu.from_llm(data, message, today)
            except (LLMUnavailable, LLMOutputError, SchemaError, ValueError) as exc:
                found = nlu.parse_intent(message, today, n, reason=nlu.validation_reason(exc))
            else:
                if dropped:
                    found = found.model_copy(update={"fallback_reason": "dropped_unstated:" + ",".join(dropped)})
                # Escalation signals the parser reads (a person, an out-of-scope topic) apply whatever the model
                # said: they can only make the outcome stricter, like narrow().
                strict = nlu.parse_intent(message, today, n)
                if strict.intent in ("request_human", "out_of_scope") and found.intent != strict.intent:
                    found = strict.model_copy(update={"fallback_reason": f"parser_escalation_over_llm:{found.intent}"})
        self._step(turn, "understand", found.source,
                   {"intent": found.intent, "fallback_reason": found.fallback_reason, "topic": found.topic,
                    "amount": found.amount, "currency": found.currency,
                    "date": found.date.isoformat() if found.date else None, "merchant": found.merchant,
                    "selected_option": found.selected_option, "record_ref": found.record_ref}, started=started)
        return found

    # ---- route ---------------------------------------------------------------------------------------------
    def _route(self, turn: Turn, message: str) -> None:
        state = turn.state
        marker = nlu.detect_injection(message)
        if marker:
            self._step(turn, "understand", "injection_detector", {"marker": marker})
            self._security(turn, "prompt_injection", marker)
            return
        foreign = [r for r in nlu.find_refs(message)
                   if nlu.ref_kind(r) == "customer" and r != turn.session.customer_id]
        if foreign:
            self._security(turn, "cross_customer_reference", "customer id of another customer in the message")
            return
        found = self._understand(turn, message)
        if found.intent == "request_human":
            self._escalate(turn, "customer_requested_human", self._policy_only(human=True).rule_ids)
            return
        if state.pending is not None:
            self._say(turn, "pending_confirmation", {"label": state.pending.label}, (state.pending.label,))
            return
        refs = [r for r in dict.fromkeys([*(filter(None, [found.record_ref])), *nlu.find_refs(message)])
                if nlu.ref_kind(r) != "customer"]
        if refs:
            self._from_reference(turn, refs[0], found)
            return
        if found.intent == "out_of_scope":
            decision = self._policy_only(intent=found.topic or "other_customer_request")
            self._escalate(turn, "out_of_scope", decision.rule_ids, topic=found.topic)
            return
        if found.intent == "block_card":
            self._block_flow(turn, None)
            return
        if state.options and found.intent == "select_option" and found.selected_option:
            if found.selected_option <= len(state.options):
                self._select(turn, state.options[found.selected_option - 1])
                return
        if state.options and found.intent == "reject_options":
            state.options = []
        state.statements.append(message)
        state.slots.merge(found)
        self._decide(turn)

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

    # ---- decide (learned disposition) ------------------------------------------------------------------------
    def _decide(self, turn: Turn) -> None:
        state = turn.state
        pool_result = self._tool(turn, "list_recent_transactions", POOL_ARGS)
        if not pool_result.ok:
            self._tool_problem(turn, pool_result)
            return
        pool = pool_result.data["transactions"]
        cues = self._cues(state, pool)
        if not cues:
            self._clarify_details(turn, cues)
            return
        started = time.perf_counter()
        disposition = self.disposition.decide(state.text, self.service.clock().date(), state.slots.overrides(), pool)
        state.confidence = disposition.confidence
        self._step(turn, "decide.disposition", disposition.decision,
                   {"model": disposition.model, "confidence": disposition.confidence, "cues": sorted(cues),
                    "pool_size": len(pool), "probabilities": disposition.probabilities}, started=started)
        if disposition.decision == "resolve":
            self._act_on_transaction(turn, disposition.top_k[0], disposition.confidence, disposition.model)
            return
        if disposition.decision == "escalate" or state.clarify_rounds >= MAX_CLARIFY_ROUNDS:
            state.add_fact(f"Searched {len(pool)} own transactions of the last 90 days; the disposition model "
                           f"({disposition.model}) found no single charge that fits",
                           f"list_recent_transactions:{turn.trace_id}")
            self._escalate(turn, "low_confidence", [], confidence=disposition.confidence)
            return
        by_id = {tx["transaction_id"]: tx for tx in pool}
        state.options = [Option(i + 1, "transaction", tid, tx_label(by_id[tid]))
                         for i, tid in enumerate(disposition.top_k[:3]) if tid in by_id]
        state.clarify_rounds += 1
        state.stage = "clarifying"
        listing = "\n".join(f"{o.index}) {o.label}" for o in state.options)
        self._say(turn, "clarify_options", {"options": listing}, tuple(o.label for o in state.options))

    def _cues(self, state, pool: list[dict[str, Any]]) -> set[str]:
        slots = state.slots
        cues = {k for k, v in (("amount", slots.amount), ("date", slots.date), ("merchant", slots.merchant)) if v}
        cues |= nlu.text_cues(state.text, self.service.clock().date())
        tokens = tuple(w.strip(".,;:?!") for w in nlu.plain(state.text).split() if len(w) >= 3)
        if any(merchant_similarity(tokens, tx.get("merchant_name"))[0] >= MERCHANT_MENTION_MIN for tx in pool):
            cues.add("merchant")
        return cues

    def _clarify_details(self, turn: Turn, cues: set[str]) -> None:
        state = turn.state
        if state.clarify_rounds >= MAX_CLARIFY_ROUNDS:
            self._escalate(turn, "low_confidence", [])
            return
        state.clarify_rounds += 1
        state.stage = "clarifying"
        self._say(turn, "ask_details", {"missing": self._missing_text(state, cues)})

    @staticmethod
    def _missing_text(state, cues: set[str]) -> str:
        names = replies.CUES[state.language]
        missing = [names[k] for k in ("amount", "date", "merchant") if k not in cues]
        missing = missing or list(names.values())
        last = " o " if state.language == "es" else " ou "
        return missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + last + missing[-1]

    # ---- finish ----------------------------------------------------------------------------------------------
    def _finish(self, turn: Turn) -> TurnResult:
        state, reply = turn.state, turn.reply or replies.Reply("", "template")
        state.transcript.append(("assistant", reply.text))
        llm = self._llm_usage(turn.trace_id)
        latency = (time.perf_counter() - turn.started) * 1000
        self.service.audit.record(trace_id=turn.trace_id, step="orchestrator.turn", outcome=state.stage,
                                  latency_ms=latency, customer_ref=state.customer_ref,
                                  rule_ids=[r for s in turn.steps for r in s.rule_ids],
                                  args={"reply_source": reply.source, "llm_calls": llm["calls"],
                                        "llm_cost_usd": llm["cost_usd"], "llm_input_tokens": llm["input_tokens"],
                                        "llm_output_tokens": llm["output_tokens"]})
        pending = state.pending
        confirmation = None if pending is None else {
            "confirmation_id": pending.confirmation_id, "tool": pending.tool, "label": pending.label,
            "expires_at": pending.expires_at.isoformat(), "review": pending.must_escalate}
        case = None if not state.case_id else {
            "case_id": state.case_id,
            "verified": any(a == "open_dispute_case" and s == "verified" for a, s, _ in state.actions)}
        return TurnResult(state.conversation_id, turn.trace_id, state.language, state.stage, reply.text,
                          reply.source, list(state.options), confirmation, case, state.handoff, list(turn.steps),
                          None, llm, round(latency, 1))
