"""The single entry point for tool calls: permission layer, execution with retries, verify step, audit.

Whatever produces a tool call (an LLM, a deterministic workflow, an evaluation harness or an attacker who controls
the model) goes through ToolService.execute and gets the same enforcement. Every call writes audit records,
including denials. A failure that survives bounded retries, or a write that cannot be verified, never reaches the
customer as a success: the result carries handoff_required with reason tool_failure.

Security flags: a cross-customer access attempt or a replayed request marks the session. From then on the policy
engine removes every write action for that session and escalates with security_event.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.clock import Clock, system_clock
from agent.security.audit import AuditLog, new_trace_id
from agent.security.permissions import ConfirmationChallenge, ConfirmationService, GuardDenied, PermissionGuard
from agent.security.session import AuthError, IdentityService, LoginChallenge, Session, SessionGrant
from agent.security.signing import Signer
from agent.tools.contracts import ERROR_CODES, ToolError, ToolResult
from agent.tools.faults import RetriesExhausted, RetryPolicy
from agent.tools.impl import ToolContext, ToolFailure, WriteOutcome
from agent.tools.ranking import CandidateRanker, RuleBasedRanker
from agent.tools.repository import CaseStore, WarehouseRepository


class ToolService:
    def __init__(self, secret: bytes, identity: IdentityService, repo: WarehouseRepository, cases: CaseStore,
                 audit: AuditLog, clock: Clock = system_clock, retry: RetryPolicy | None = None,
                 sleep: Callable[[float], None] = time.sleep, ranker: CandidateRanker | None = None) -> None:
        self.identity = identity
        self.repo = repo
        self.cases = cases
        self.audit = audit
        self.clock = clock
        self.retry = retry or RetryPolicy()
        self.sleep = sleep
        self.ranker = ranker or RuleBasedRanker()
        self._digests = Signer(secret, "idempotency")
        self.confirmations = ConfirmationService(secret, clock)
        self.guard = PermissionGuard(identity, repo, cases, self.confirmations, clock)
        self.security_flags: dict[str, set[str]] = {}
        self._names: dict[str, tuple[str, ...]] = {}

    # ---- login, audited -------------------------------------------------------------------------------
    def start_login(self, document_number: str, trace_id: str | None = None) -> LoginChallenge:
        trace_id = trace_id or new_trace_id()
        challenge = self.identity.start_login(document_number)
        self.audit.record(trace_id=trace_id, step="auth.start_login", outcome="challenge_issued",
                          args={"document_number": document_number})
        return challenge

    def verify_otp(self, challenge_id: str, code: str, trace_id: str | None = None) -> SessionGrant:
        trace_id = trace_id or new_trace_id()
        try:
            grant = self.identity.verify_otp(challenge_id, code)
        except AuthError as exc:
            self.audit.record(trace_id=trace_id, step="auth.verify_otp", outcome="denied", reason=exc.code,
                              args={"challenge_id": challenge_id})
            raise
        self.audit.record(trace_id=trace_id, step="auth.verify_otp", outcome="session_issued",
                          args={"challenge_id": challenge_id}, customer_ref=grant.session.customer_ref)
        return grant

    # ---- confirmation ---------------------------------------------------------------------------------
    def request_confirmation(self, session_token: str, request_id: str, tool: str, args: Mapping[str, Any],
                             trace_id: str | None = None) -> ConfirmationChallenge | ToolResult:
        """Issue a confirmation only for a call that would pass every other check."""
        trace_id = trace_id or new_trace_id()
        try:
            session = self.guard.authenticate(session_token, request_id)
            _, parsed, decision = self.guard.preconditions(tool, args, session, self._flags(session.session_id))
        except GuardDenied as exc:
            return self._denied(trace_id, tool, args, exc, session_token)
        challenge = self.confirmations.issue(session, tool, parsed, self.customer_names(session))
        self.audit.record(trace_id=trace_id, step="confirmation.issue", tool=tool, args=dict(args),
                          outcome="issued", rule_ids=decision.rule_ids if decision else (),
                          customer_ref=session.customer_ref, known_names=self.customer_names(session))
        return challenge

    # ---- tool calls -----------------------------------------------------------------------------------
    def execute(self, tool: str, args: Mapping[str, Any], session_token: str, request_id: str,
                confirmation_token: str | None = None, trace_id: str | None = None) -> ToolResult:
        trace_id = trace_id or new_trace_id()
        start = time.perf_counter()
        try:
            session = self.guard.authenticate(session_token, request_id)
            auth = self.guard.authorize(tool, args, session, confirmation_token, self._flags(session.session_id))
        except GuardDenied as exc:
            return self._denied(trace_id, tool, args, exc, session_token)
        rule_ids = auth.policy.rule_ids if auth.policy else []
        self.audit.record(trace_id=trace_id, step="guard", tool=tool, args=dict(args), outcome="allowed",
                          rule_ids=rule_ids, customer_ref=session.customer_ref,
                          reason="confirmed" if auth.confirmation_id else None,
                          known_names=self.customer_names(session))
        ctx = ToolContext(repo=self.repo, cases=self.cases, session=session, clock=self.clock, trace_id=trace_id,
                          digests=self._digests, retry=self.retry, sleep=self.sleep, ranker=self.ranker,
                          security_flags=frozenset(self._flags(session.session_id)),
                          known_names=self.customer_names(session))
        result = self._run(ctx, auth.spec, auth.args, tool, rule_ids)
        self.audit.record(trace_id=trace_id, step="tool", tool=tool, args=dict(args),
                          outcome=self._outcome(result), reason=result.error.code if result.error else None,
                          rule_ids=rule_ids, latency_ms=(time.perf_counter() - start) * 1000,
                          customer_ref=session.customer_ref, attempts=result.attempts,
                          known_names=self.customer_names(session))
        return result

    def _run(self, ctx: ToolContext, spec, args, tool: str, rule_ids: list[str]) -> ToolResult:
        base = {"trace_id": ctx.trace_id, "tool": tool, "rule_ids": rule_ids}
        try:
            output = spec.handler(ctx, args)
        except RetriesExhausted as exc:
            return ToolResult(**base, ok=False, attempts=exc.attempts + ctx.attempts,
                              error=ToolError(code="tool_unavailable", message=ERROR_CODES["tool_unavailable"]),
                              handoff_required=True, handoff_reason="tool_failure")
        except ToolFailure as exc:
            return ToolResult(**base, ok=False, attempts=max(ctx.attempts, 1),
                              error=ToolError(code=exc.code, message=ERROR_CODES.get(exc.code, exc.code)))
        if isinstance(output, WriteOutcome):
            verification = "verified" if output.verified else "not_verified"
            self.audit.record(trace_id=ctx.trace_id, step="verify", tool=tool, args={"read_back": tool},
                              outcome=verification, customer_ref=ctx.session.customer_ref)
            if not output.verified:
                return ToolResult(**base, ok=False, data=output.data, verification="not_verified",
                                  error=ToolError(code="not_verified", message=ERROR_CODES["not_verified"]),
                                  handoff_required=True, handoff_reason="tool_failure", attempts=ctx.attempts)
            return ToolResult(**base, ok=True, data=output.data, verification="verified", attempts=ctx.attempts)
        return ToolResult(**base, ok=True, data=output.model_dump(mode="json"), attempts=ctx.attempts)

    def customer_names(self, session: Session) -> tuple[str, ...]:
        """The session customer's own names, used to mask them in free text (audit, confirmations, handoffs)."""
        if session.session_id not in self._names:
            customer = self.repo.get_customer(session.customer_id, faulted=False) or {}
            parts = [customer.get("first_name"), customer.get("last_name")]
            self._names[session.session_id] = tuple(
                p for part in parts if part for p in {str(part), *str(part).split()} if len(p) >= 3)
        return self._names[session.session_id]

    # ---- helpers --------------------------------------------------------------------------------------
    def _flags(self, session_id: str) -> set[str]:
        return self.security_flags.setdefault(session_id, set())

    def _denied(self, trace_id: str, tool: str, args: Mapping[str, Any], exc: GuardDenied,
                session_token: str) -> ToolResult:
        customer_ref, names = None, ()
        try:
            session = self.identity.validate(session_token)
            customer_ref, names = session.customer_ref, self.customer_names(session)
            if exc.security_flag:
                self._flags(session.session_id).add(exc.security_flag)
        except AuthError:
            pass
        raw = dict(args) if isinstance(args, Mapping) else {"unparsed": str(args)[:200]}
        self.audit.record(trace_id=trace_id, step="guard", tool=tool, args=raw,
                          outcome="denied", reason=exc.reason, rule_ids=exc.rule_ids, customer_ref=customer_ref,
                          known_names=names)
        security = exc.security_flag is not None
        return ToolResult(trace_id=trace_id, tool=tool, ok=False, rule_ids=exc.rule_ids,
                          error=ToolError(code=exc.code, message=ERROR_CODES.get(exc.code, exc.code)),
                          handoff_required=security, handoff_reason="security_event" if security else None)

    @staticmethod
    def _outcome(result: ToolResult) -> str:
        if result.handoff_required:
            return "handoff_required"
        return "ok" if result.ok else "error"
