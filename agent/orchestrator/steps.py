"""Shared machinery for every lifecycle step: the per-turn context, tool calls through ToolService, the decision
trail and its audit records, replies, and the escalation step.

Tool calls always go through ToolService.execute with a fresh request id, so the permission layer, retries,
verification and audit apply to the orchestrator exactly as to any other caller. The session token and the
confirmation token are passed to the service only; neither is written to the trail, a reply or a prompt.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.llm.port import LanguageModel
from agent.orchestrator import handoffs, replies
from agent.orchestrator.disposition import DispositionModel
from agent.orchestrator.state import ConversationState, ConversationStore, TrailStep
from agent.policy.engine import PolicyDecision, PolicyInput, evaluate
from agent.security.session import Session
from agent.service import ToolService
from agent.tools.contracts import ToolResult

HandoffSink = Callable[[ConversationState, dict[str, Any]], None]


@dataclass
class Turn:
    state: ConversationState
    token: str
    session: Session
    trace_id: str
    started: float = field(default_factory=time.perf_counter)
    steps: list[TrailStep] = field(default_factory=list)
    reply: replies.Reply | None = None


def request_id() -> str:
    return "rq_" + secrets.token_hex(10)


def tx_label(view: Mapping[str, Any]) -> str:
    when = str(view.get("transaction_date") or "")[:10]
    day = f"{when[8:10]}/{when[5:7]}/{when[:4]}" if len(when) == 10 else "?"
    who = view.get("merchant_name") or view.get("transaction_type") or "?"
    amount = view.get("amount")
    money = f"{float(amount):.2f} {view.get('currency') or ''}".strip() if amount is not None else "?"
    return f"{day}, {who}, {money}"


def card_label(product: Mapping[str, Any]) -> str:
    return "**** " + str(product.get("product_number_masked") or "")[-4:]


class StepsMixin:
    service: ToolService
    llm: LanguageModel | None
    disposition: DispositionModel
    store: ConversationStore
    handoff_sink: HandoffSink | None

    # ---- trail and audit ------------------------------------------------------------------------------
    def _step(self, turn: Turn, step: str, outcome: str, detail: Mapping[str, Any] | None = None,
              rule_ids: list[str] | None = None, started: float | None = None) -> None:
        latency = (time.perf_counter() - started) * 1000 if started is not None else 0.0
        entry = TrailStep(turn.trace_id, step, outcome, dict(detail or {}), list(rule_ids or []), latency)
        turn.steps.append(entry)
        turn.state.trail.append(entry)
        self.service.audit.record(trace_id=turn.trace_id, step=f"orchestrator.{step}", outcome=outcome,
                                  args=entry.detail, rule_ids=entry.rule_ids, latency_ms=latency,
                                  customer_ref=turn.state.customer_ref, known_names=self._names(turn))

    def _names(self, turn: Turn) -> tuple[str, ...]:
        return self.service.customer_names(turn.session)

    # ---- tools ------------------------------------------------------------------------------------------
    def _tool(self, turn: Turn, tool: str, args: Mapping[str, Any], confirmation: str | None = None) -> ToolResult:
        started = time.perf_counter()
        result = self.service.execute(tool, dict(args), turn.token, request_id(), confirmation, turn.trace_id)
        detail: dict[str, Any] = {"tool": tool, "ok": result.ok, "attempts": result.attempts}
        if result.error:
            detail["error"] = result.error.code
        if result.verification:
            detail["verification"] = result.verification
        self._step(turn, f"tool.{tool}", "ok" if result.ok else "error", detail, result.rule_ids, started)
        return result

    def _policy_only(self, intent: str = "unrecognized_charge", human: bool = False,
                     flags: tuple[str, ...] = ()) -> PolicyDecision:
        """Policy rules that do not depend on a transaction (scope, human request, security)."""
        return evaluate(PolicyInput(intent=intent, customer_country=None, as_of=self.service.clock(),
                                    customer_requested_human=human, security_flags=list(flags)))

    # ---- replies ----------------------------------------------------------------------------------------
    def _say(self, turn: Turn, kind: str, fields: Mapping[str, Any] | None = None,
             must_mention: tuple[str, ...] = ()) -> replies.Reply:
        started = time.perf_counter()
        reply = replies.compose(self.llm, kind, turn.state.language, fields or {}, must_mention,
                                self._names(turn), turn.trace_id)
        self._step(turn, "reply", reply.source, {"kind": kind, "note": reply.note}, started=started)
        turn.reply = reply
        return reply

    # ---- escalation -------------------------------------------------------------------------------------
    def _escalate(self, turn: Turn, reason_code: str, rule_ids: list[str], *, confidence: float | None = None,
                  topic: str | None = None, questions: list[str] | None = None) -> replies.Reply:
        started = time.perf_counter()
        state = turn.state
        state.add_rules(rule_ids)
        document = handoffs.assemble(state, trace_id=turn.trace_id, reason_code=reason_code, rule_ids=rule_ids,
                                     now=self.service.clock(), known_names=self._names(turn), topic=topic,
                                     confidence=confidence, extra_questions=questions)
        state.handoff, state.stage, state.options, state.pending = document, "handed_off", [], None
        self._step(turn, "escalate", reason_code, {"handoff_id": document["handoff_id"],
                                                   "facts": len(document["verified_facts"]),
                                                   "actions": len(document["actions_taken"])}, rule_ids, started)
        if self.handoff_sink:
            self.handoff_sink(state, document)
        lang = state.language
        case = replies.CASE_NOTE[lang].format(case_id=state.case_id) if state.case_id else ""
        mention = (state.case_id,) if state.case_id else ()
        return self._say(turn, "handed_off", {"reason": replies.reason_text(reason_code, lang), "case": case},
                         mention)

    def _security(self, turn: Turn, flag: str, detail: str) -> replies.Reply:
        """Flag the session (policy then removes every write) and transfer with security_event (SYN-SEC-001)."""
        flags = self.service.security_flags.setdefault(turn.session.session_id, set())
        flags.add(flag)
        decision = self._policy_only(flags=tuple(sorted(flags)))
        self._step(turn, "security", flag, {"marker": detail}, decision.rule_ids)
        return self._escalate(turn, "security_event", decision.rule_ids)

    def _tool_problem(self, turn: Turn, result: ToolResult, action: str | None = None) -> replies.Reply:
        """A tool call that did not succeed: security event, tool failure, or an expired session mid-turn."""
        if result.handoff_reason == "security_event":
            return self._security(turn, "cross_customer_access", f"{result.tool}:{result.error.code}")
        if action:
            status = "not_verified" if result.verification == "not_verified" else "failed"
            record = (result.data or {}).get("case_id") or (result.data or {}).get("block_id")
            turn.state.actions.append((action, status, record))
        return self._escalate(turn, "tool_failure", result.rule_ids,
                              questions=[f"{result.tool} returned {result.error.code if result.error else 'error'} "
                                         f"after {result.attempts} attempt(s)."])

    # ---- usage ------------------------------------------------------------------------------------------
    def _llm_usage(self, trace_id: str) -> dict[str, Any]:
        usage = getattr(self.llm, "usage", None)
        calls = [c for c in (usage.calls if usage else []) if c.trace_id == trace_id]
        costs = [c.cost_usd for c in calls]
        return {"calls": len(calls), "failed": sum(not c.ok for c in calls),
                "input_tokens": sum(c.input_tokens or 0 for c in calls),
                "output_tokens": sum(c.output_tokens or 0 for c in calls),
                "latency_ms": round(sum(c.latency_ms for c in calls), 1),
                "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
                "provider": getattr(getattr(self.llm, "adapter", None), "provider", None),
                "model": getattr(getattr(self.llm, "adapter", None), "model", None)}
