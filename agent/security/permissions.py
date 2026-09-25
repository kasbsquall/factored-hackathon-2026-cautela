"""Permission layer: every tool call passes here before it runs. Deny by default.

Order of checks (the first failure stops the call):
  1. allowlist      the tool must be registered in agent/tools/registry.py
  2. session        signed, unexpired, not revoked (IdentityService.validate)
  3. replay         the request id is new for this session
  4. contract       arguments validate against the tool's input model; unknown fields such as customer_id fail
  5. ownership      every referenced record belongs to the session customer
  6. policy         write actions must be allowed by the policy engine for the verified facts
  7. confirmation   actions listed in rules.yaml need a customer confirmation token bound to this session, this
                    tool and these exact arguments; it is consumed only after every other check passed

Records owned by someone else are reported to the caller as `not_found`, the same answer as a record that does
not exist, so the response is not an oracle for other customers' ids. The audit log keeps the real reason.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.clock import Clock
from agent.policy.engine import PolicyDecision, load_rules
from agent.security.pii import mask_mapping
from agent.security.session import AuthError, IdentityService, Session
from agent.security.signing import Signer
from agent.tools.facts import policy_decision
from agent.tools.registry import TOOLS, ToolSpec
from agent.tools.repository import CaseStore, WarehouseRepository

CONFIRMATION_TTL = timedelta(minutes=5)


class GuardDenied(Exception):
    def __init__(self, code: str, reason: str | None = None, rule_ids: Iterable[str] = (),
                 security_flag: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason or code
        self.rule_ids = list(rule_ids)
        self.security_flag = security_flag


@dataclass(frozen=True)
class ConfirmationChallenge:
    """Shown to the customer as an explicit yes/no. The token goes to the UI button, not to the model."""

    confirmation_id: str
    token: str
    tool: str
    summary: dict[str, Any]
    expires_at: datetime


@dataclass(frozen=True)
class Authorization:
    spec: ToolSpec
    session: Session
    args: BaseModel
    policy: PolicyDecision | None
    confirmation_id: str | None


class ConfirmationService:
    def __init__(self, secret: bytes, clock: Clock) -> None:
        self._signer = Signer(secret, "confirmation")
        self._clock = clock
        self._consumed: set[str] = set()

    def _args_digest(self, tool: str, args: BaseModel) -> str:
        return self._signer.digest({"tool": tool, "args": args.model_dump(mode="json")})

    def issue(self, session: Session, tool: str, args: BaseModel) -> ConfirmationChallenge:
        now = self._clock()
        cid = secrets.token_urlsafe(12)
        expires = now + CONFIRMATION_TTL
        token = self._signer.sign({"cid": cid, "sid": session.session_id, "tool": tool,
                                   "args": self._args_digest(tool, args), "exp": int(expires.timestamp())})
        return ConfirmationChallenge(cid, token, tool, mask_mapping(args.model_dump(mode="json")), expires)

    def consume(self, session: Session, tool: str, args: BaseModel, token: str) -> str:
        payload = self._signer.verify(token)
        if payload is None:
            raise GuardDenied("confirmation_invalid", "confirmation_tampered")
        checks = [
            (payload.get("cid") in self._consumed, "confirmation_already_used"),
            (payload.get("sid") != session.session_id, "confirmation_other_session"),
            (payload.get("tool") != tool, "confirmation_other_tool"),
            (payload.get("args") != self._args_digest(tool, args), "confirmation_args_mismatch"),
            (self._clock().timestamp() >= float(payload.get("exp", 0)), "confirmation_expired"),
        ]
        for failed, reason in checks:
            if failed:
                raise GuardDenied("confirmation_invalid", reason)
        self._consumed.add(payload["cid"])
        return payload["cid"]


class PermissionGuard:
    def __init__(self, identity: IdentityService, repo: WarehouseRepository, cases: CaseStore,
                 confirmations: ConfirmationService, clock: Clock, rules: dict | None = None) -> None:
        self.identity = identity
        self.repo = repo
        self.cases = cases
        self.confirmations = confirmations
        self._clock = clock
        self._rules = rules or load_rules()
        self.confirmation_actions = frozenset(self._rules["confirmations"]["actions"])

    def authenticate(self, session_token: str, request_id: str) -> Session:
        try:
            session = self.identity.validate(session_token)
            self.identity.consume_request_id(session, request_id)
        except AuthError as exc:
            flag = "replay" if exc.code == "replay_detected" else None
            raise GuardDenied(exc.code, security_flag=flag) from exc
        return session

    def _owner(self, kind: str, record_id: str) -> str | None:
        return self.cases.owner_of_case(record_id) if kind == "case" else self.repo.owner_of(kind, record_id)

    def preconditions(self, tool: str, raw_args: Mapping[str, Any], session: Session,
                      security_flags: Iterable[str] = ()) -> tuple[ToolSpec, BaseModel, PolicyDecision | None]:
        """Checks 1, 4, 5 and 6. Shared by authorize() and by confirmation issuing."""
        spec = TOOLS.get(tool)
        if spec is None:
            raise GuardDenied("tool_not_allowed")
        if not isinstance(raw_args, Mapping):
            raise GuardDenied("validation_error", "arguments must be an object")
        try:
            args = spec.input_model.model_validate(dict(raw_args))
        except ValidationError as exc:
            fields = sorted({".".join(map(str, e["loc"])) or "_" for e in exc.errors()})
            raise GuardDenied("validation_error", f"invalid fields: {', '.join(fields)}") from exc
        for arg, kind in spec.owned_args.items():
            owner = self._owner(kind, getattr(args, arg))
            if owner is None:
                raise GuardDenied("not_found", "record_not_found")
            if owner != session.customer_id:
                raise GuardDenied("not_found", "ownership_violation", security_flag="cross_customer_access")
        decision = None
        if spec.kind == "write":
            decision = policy_decision(
                self.repo, self.cases, session.customer_id, self._clock(),
                transaction_id=getattr(args, "transaction_id", None), product_id=getattr(args, "product_id", None),
                security_flags=security_flags)
            if not decision.allows(tool):
                raise GuardDenied("policy_denied", "policy_denied", decision.rule_ids)
        return spec, args, decision

    def authorize(self, tool: str, raw_args: Mapping[str, Any], session: Session,
                  confirmation_token: str | None = None, security_flags: Iterable[str] = ()) -> Authorization:
        spec, args, decision = self.preconditions(tool, raw_args, session, security_flags)
        confirmation_id = None
        if tool in self.confirmation_actions:
            rule = self._rules["confirmations"]["id"]
            if not confirmation_token:
                raise GuardDenied("confirmation_required", rule_ids=[rule])
            try:
                confirmation_id = self.confirmations.consume(session, tool, args, confirmation_token)
            except GuardDenied as exc:
                exc.rule_ids = [rule]
                raise
        return Authorization(spec, session, args, decision, confirmation_id)
