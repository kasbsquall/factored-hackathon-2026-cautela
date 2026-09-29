"""The request lifecycle: session gate -> understand -> decide -> recognize -> act -> verify -> escalate.

    orchestrator = Orchestrator(service, llm=None | MaskedLLM, disposition=load_default())
    result = orchestrator.turn(session_token, "No reconozco un cargo de 350 pesos del 27 de mayo")
    result = orchestrator.recognize(session_token, result.conversation_id,
                                    result.recognition["recognition_id"], recognized=False)
    result = orchestrator.confirm(session_token, result.conversation_id, result.confirmation["confirmation_id"])

The model interprets (extract) and phrases (reply). Everything else is code: the session gate, ownership and
confirmation checks (ToolService), the policy decision (rules.yaml), the charge evidence and match reasons shown
to the customer (evidence.py, tool fields and ranker features only), the verify read-back and the handoff. Each
step appends to the decision trail and writes an audit record under the turn's trace id, with rule ids, tool
calls, latency, and the LLM tokens and cost of the turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from agent.llm.port import LanguageModel
from agent.orchestrator import evidence, fmt, handoffs, replies
from agent.orchestrator import intent as nlu
from agent.orchestrator.disposition import (DISPUTABLE_STATUSES, Disposition, DispositionModel, load_default,
                                             non_disputable_fit, plausible_charges)
from agent.orchestrator.routing import RoutingMixin, names_a_charge
from agent.orchestrator.state import FINAL_STAGES, ConversationStore, Option, TrailStep
from agent.orchestrator.steps import HandoffSink, Turn, tx_label
from agent.orchestrator.unmatched import MAX_CLARIFY_ROUNDS
from agent.security.audit import new_trace_id
from agent.security.session import AuthError
from agent.service import ToolService
from ml.features.pairwise import merchant_similarity

MAX_MESSAGE_CHARS = 2000
POOL_ARGS = {"window_days": 90, "limit": 50}
MERCHANT_MENTION_MIN = 0.88  # 0.8 let 'conta' match 'Conecta' (0.83) in a pt run


def mentions_merchant(tokens: tuple[str, ...], merchant: str | None) -> bool:
    """Whether the text names this merchant: a close spelling of one of its words that starts with the same letter.
    The first-letter rule keeps ordinary words that contain a merchant word out ("atienda" is not "Tienda")."""
    if not merchant:
        return False
    words = [w for w in nlu.plain(merchant).split() if len(w) >= 4] or nlu.plain(merchant).split()
    return any(merchant_similarity((t,), w)[0] >= MERCHANT_MENTION_MIN and t[:1] == w[:1]
               for t in tokens for w in words)


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
    recognition: dict[str, Any] | None = None


class Orchestrator(RoutingMixin):
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
        self.service.audit.bind(trace_id, state.conversation_id)  # every record of this turn carries the conversation
        state.transcript.append(("customer", message))
        self._step(turn, "gate", "session_valid", {"conversation_id": state.conversation_id})
        if state.stage in FINAL_STAGES:
            self._guarded(turn, lambda: self._closed_turn(turn, message))
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
        self.service.audit.bind(trace_id, state.conversation_id)  # every record of this turn carries the conversation
        state.pending = None
        self._step(turn, "confirm.answer", "accepted" if accept else "declined",
                   {"confirmation_id": confirmation_id, "tool": pending.tool})
        if accept:
            self._guarded(turn, lambda: self._execute_confirmed(turn, pending))
        else:
            state.stage = "closed"
            self._say(turn, "declined")
        return self._finish(turn)

    def recognize(self, session_token: str, conversation_id: str, recognition_id: str,
                  recognized: bool) -> TurnResult:
        """The customer's answer to "do you recognize this charge?". Recognized: the conversation ends with no
        dispute and no write. Not recognized: the dispute confirmation is issued."""
        trace_id, started = new_trace_id(), time.perf_counter()
        session = self._gate(session_token, trace_id)
        if isinstance(session, TurnResult):
            return session
        state = self.store.get(conversation_id, session.customer_id)
        if state is None:
            return self._error(trace_id, "conversation_not_found", "es")
        check = state.recognition
        if check is None or check.recognition_id != recognition_id:
            return self._error(trace_id, "no_pending_recognition", state.language, state.conversation_id)
        turn = Turn(state, session_token, session, trace_id, started)
        state.trace_ids.append(trace_id)
        self.service.audit.bind(trace_id, state.conversation_id)  # every record of this turn carries the conversation
        state.recognition = None
        self._step(turn, "recognize.answer", "recognized" if recognized else "not_recognized",
                   {"recognition_id": recognition_id, "transaction_id": check.transaction_id})
        self._guarded(turn, lambda: self._after_recognition(turn, check, recognized))
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

    # ---- decide (learned disposition) ------------------------------------------------------------------------
    def _decide(self, turn: Turn) -> None:
        state = turn.state
        pool_result = self._tool(turn, "list_recent_transactions", POOL_ARGS)
        if not pool_result.ok:
            self._tool_problem(turn, pool_result)
            return
        pool = pool_result.data["transactions"]
        cues = self._cues(state, pool)
        state.cued = names_a_charge(cues, state.text)
        state.searched, state.plausible = len(pool), []
        if not state.cued:  # nothing names a charge: never act on one (see actions._act_on_transaction)
            self._clarify_details(turn, cues)
            return
        started, text = time.perf_counter(), nlu.cue_text(state.text, self.service.clock().date())
        state.plausible = plausible_charges(text, self.service.clock().date(), state.slots.overrides(), pool)
        spent = non_disputable_fit(text, self.service.clock().date(), state.slots.overrides(), pool)
        rivals = self._money_moved_rivals(turn, pool, spent["transaction_id"]) if spent is not None else []
        if spent is not None:  # a charge that moved no money: policy explains it, no model is asked
            self._step(turn, "decide.status_check", "disputable_rival" if rivals else "fits_non_disputable_charge",
                       {"status": spent.get("transaction_status"), "cues": sorted(cues), "pool_size": len(pool),
                        "rivals": rivals[:3]}, started=started)
            if not rivals:
                self._act_on_transaction(turn, spent["transaction_id"], None,
                                         "status check (the only charge that fits every cue moved no money)")
                return
        disposition = self.disposition.decide(text, self.service.clock().date(), state.slots.overrides(), pool)
        state.confidence = disposition.confidence
        self._step(turn, "decide.disposition", disposition.decision,
                   {"model": disposition.model, "confidence": disposition.confidence, "cues": sorted(cues),
                    "pool_size": len(pool), "probabilities": disposition.probabilities, "note": disposition.note},
                   started=started)
        disposition = self._without_rejected(turn, disposition)
        if disposition.decision == "resolve":
            disposition = self._prefer_money_moved(turn, pool, disposition)
        if disposition.decision == "resolve":
            self._match_reasons(turn, pool, disposition.top_k[:1])
            self._act_on_transaction(turn, disposition.top_k[0], disposition.confidence, disposition.model)
            return
        if disposition.decision == "escalate" and state.clarify_rounds < MAX_CLARIFY_ROUNDS:
            disposition = self._plausible_instead(turn, disposition)
        if disposition.decision == "escalate" or state.clarify_rounds >= MAX_CLARIFY_ROUNDS:
            self._transfer_unmatched(turn, disposition.confidence)
            return
        by_id = {tx["transaction_id"]: tx for tx in pool}
        shown = [tid for tid in disposition.top_k[:3] if tid in by_id]
        if rivals:  # a declined charge fits every cue: the charges that moved money and fit as well come first
            shown = list(dict.fromkeys([*rivals[:2], *shown]))[:3]
        reasons, cards = self._match_reasons(turn, pool, shown), self._cards(turn)
        state.options = [Option(i + 1, "transaction", tid, tx_label(by_id[tid]),
                                evidence.charge_details(by_id[tid], cards), tuple(reasons.get(tid, [])))
                         for i, tid in enumerate(shown)]
        state.clarify_rounds += 1
        state.stage = "clarifying"
        listing = "\n".join(f"{o.index}) {fmt.label_text(o.label, state.language)}" for o in state.options)
        self._say(turn, "clarify_options", {"options": listing}, tuple(o.label for o in state.options))

    def _money_moved_rivals(self, turn: Turn, pool: list[dict[str, Any]], transaction_id: str) -> list[str]:
        """Charges that moved money (approved or pending) and match the stated cues at least as well as this one,
        which did not: plausible (`disposition.plausible_charges`), not rejected, and at least as strong a fit
        (`evidence.fit_strengths`). Best fit first."""
        status = {t["transaction_id"]: t.get("transaction_status") for t in pool}
        rejected = {rid for rid, _ in turn.state.rejected}
        near = [tid for tid in turn.state.plausible if tid != transaction_id and tid not in rejected
                and status.get(tid) in DISPUTABLE_STATUSES]
        if not near:
            return []
        state, today = turn.state, self.service.clock().date()
        strength = evidence.fit_strengths(nlu.cue_text(state.text, today), today, state.slots.overrides(), pool)
        rivals = [tid for tid in near if strength.get(tid, 0) >= strength.get(transaction_id, 0)]
        return sorted(rivals, key=lambda tid: -strength[tid])

    def _prefer_money_moved(self, turn: Turn, pool: list[dict[str, Any]], disposition: Disposition) -> Disposition:
        """Never answer "no money moved" while a charge that moved money matches: a resolve on a declined or reversed
        charge with such a rival becomes a numbered list, the charges that moved money first."""
        top = next((t for t in pool if t["transaction_id"] == disposition.top_k[0]), None)
        if top is None or top.get("transaction_status") in DISPUTABLE_STATUSES:
            return disposition
        rivals = self._money_moved_rivals(turn, pool, disposition.top_k[0])
        if not rivals:
            return disposition
        self._step(turn, "decide.status_rival", "clarify", {"resolved": disposition.top_k[0], "rivals": rivals[:2]})
        return replace(disposition, decision="clarify", top_k=[*rivals[:2], disposition.top_k[0]],
                       note="money_moved_rival")

    def _without_rejected(self, turn: Turn, disposition: Disposition) -> Disposition:
        """Charges the customer answered "none of these" to are never acted on or listed again: a resolve on one
        becomes an abstention (which may still show other plausible charges), a list keeps only the others."""
        rejected = {rid for rid, _ in turn.state.rejected}
        kept = [tid for tid in disposition.top_k if tid not in rejected]
        if disposition.decision == "escalate" or kept == disposition.top_k:
            return disposition
        dropped = disposition.top_k[0] in rejected if disposition.decision == "resolve" else not kept
        self._step(turn, "decide.rejected", "escalate" if dropped else "filtered",
                   {"removed": [tid for tid in disposition.top_k if tid in rejected]})
        if dropped:
            return replace(disposition, decision="escalate", top_k=[], note="top_rejected")
        return replace(disposition, top_k=kept)

    def _plausible_instead(self, turn: Turn, disposition: Disposition) -> Disposition:
        """An abstention while some charge fits the description on every cue, or on all but one of three or more
        (`disposition.plausible_charges`), shows those charges instead of transferring. The models abstain on
        descriptions that miss their charge on one detail (an amount in another currency, "early this month" for
        the last days of the previous one); asking costs one clarifying round, and only the customer's explicit
        pick followed by "I don't recognize it" leads to a write. Charges the customer already rejected are not
        shown again. With two cues, a charge that fits one and is one misremembered detail off on the other counts
        too (`ml.recall_bounds`): an exact amount with the date four days off is asked about, not transferred."""
        rejected = {rid for rid, _ in turn.state.rejected}
        unseen = [tid for tid in turn.state.plausible if tid not in rejected]
        self._step(turn, "decide.plausible", "clarify" if unseen else "none",
                   {"plausible": len(turn.state.plausible), "shown": unseen[:3]})
        if not unseen:
            return disposition
        return replace(disposition, decision="clarify", top_k=unseen[:3], note="abstain_to_plausible")

    def _match_reasons(self, turn: Turn, pool: list[dict[str, Any]],
                       ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Why each charge matched, from the ranker features that fired (agent/orchestrator/evidence.py)."""
        state, started = turn.state, time.perf_counter()
        try:
            found = evidence.match_reasons(nlu.cue_text(state.text, self.service.clock().date()),
                                           self.service.clock().date(),
                                           state.slots.overrides(), pool,
                                           ids, state.language, POOL_ARGS["window_days"])
        except (KeyError, TypeError, ValueError) as exc:  # a charge the feature code cannot read: show no reasons
            self._step(turn, "decide.reasons", "unavailable", {"error": type(exc).__name__}, started=started)
            return {}
        state.reasons.update(found)
        self._step(turn, "decide.reasons", "computed",
                   {"reasons": {tid: [r["code"] for r in rs] for tid, rs in found.items()},
                    "features": sorted({evidence.FEATURE_OF[r["code"]] for rs in found.values() for r in rs})},
                   started=started)
        return found

    def _cues(self, state, pool: list[dict[str, Any]]) -> set[str]:
        slots = state.slots
        cues = {k for k, v in (("amount", slots.amount), ("date", slots.date), ("merchant", slots.merchant)) if v}
        cues |= nlu.text_cues(state.text, self.service.clock().date())
        tokens = tuple(w.strip(".,;:?!") for w in nlu.plain(nlu.without_refs(state.text)).split() if len(w) >= 3)
        if any(mentions_merchant(tokens, tx.get("merchant_name")) for tx in pool):
            cues.add("merchant")
        return cues

    def _clarify_details(self, turn: Turn, cues: set[str]) -> None:
        state = turn.state
        if state.clarify_rounds >= MAX_CLARIFY_ROUNDS:
            self._transfer_unmatched(turn)
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
        if state.reopened is not None and state.stage in FINAL_STAGES:  # a resumed search ended without a transfer
            case = f", case {state.case_id} filed and verified" if state.case_id else ""
            handoffs.append_question(state.reopened, f"{handoffs.FOLLOW_UP_PREFIX}corrected a detail and the resumed "
                                     f"search ended as {state.stage}{case}; nothing is pending from this transfer "
                                     "unless the customer asks again.", self._names(turn))
            state.reopened = None
        state.transcript.append(("assistant", reply.text))
        llm = self._llm_usage(turn.trace_id)
        latency = (time.perf_counter() - turn.started) * 1000
        self.service.audit.record(trace_id=turn.trace_id, step="orchestrator.turn", outcome=state.stage,
                                  latency_ms=latency, customer_ref=state.customer_ref,
                                  rule_ids=[r for s in turn.steps for r in s.rule_ids],
                                  args={"reply_source": reply.source, "llm_calls": llm["calls"],
                                        "llm_cost_usd": llm["cost_usd"], "llm_input_tokens": llm["input_tokens"],
                                        "llm_output_tokens": llm["output_tokens"]})
        return TurnResult(state.conversation_id, turn.trace_id, state.language, state.stage, reply.text,
                          reply.source, list(state.options), confirmation_view(state), case_view(state),
                          state.handoff, list(turn.steps), None, llm, round(latency, 1), recognition_view(state))


def confirmation_view(state) -> dict[str, Any] | None:
    """The pending confirmation as the customer sees it: id, label and verified charge data, never the token."""
    pending = state.pending
    if pending is None:
        return None
    return {"confirmation_id": pending.confirmation_id, "tool": pending.tool, "label": pending.label,
            "expires_at": pending.expires_at.isoformat(), "review": pending.must_escalate,
            "charge": pending.charge, "reasons": list(pending.reasons),
            "claim_window": state.claim_window if pending.tool == "open_dispute_case" else None}


def recognition_view(state) -> dict[str, Any] | None:
    check = state.recognition
    if check is None:
        return None
    return {"recognition_id": check.recognition_id, "label": check.label, "charge": check.charge,
            "reasons": list(check.reasons), "claim_window": state.claim_window}


def case_view(state) -> dict[str, Any] | None:
    if not state.case_id:
        return None
    return {"case_id": state.case_id,
            "verified": any(a == "open_dispute_case" and s == "verified" for a, s, _ in state.actions),
            "claim_window": state.claim_window}
