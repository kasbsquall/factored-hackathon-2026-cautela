"""Endpoint bodies that translate between the orchestrator or service and the API models.

Kept apart from api/app.py so the route table stays readable. Each function receives the Runtime and raises the
ApiError class it is given, so error codes stay stable and no internal detail reaches a response.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from typing import Any

from agent.llm import LLMUnavailable
from agent.orchestrator import TurnResult
from agent.orchestrator.core import confirmation_view, recognition_view
from agent.security.audit import new_trace_id
from agent.security.pii import mask_text
from agent.security.session import AuthError
from api.runtime import QueueItem, Runtime

SESSION_CODES = {"session_invalid", "session_expired", "session_revoked"}
TURN_ERRORS = {"conversation_not_found": 404, "no_pending_confirmation": 409, "no_pending_recognition": 409}
TRANSLATE_WAIT_S = 30.0  # how long a request waits for the same message's call already in flight


def verify(runtime: Runtime, body: Any, error: type) -> dict[str, Any]:
    with runtime.lock:
        try:
            grant = runtime.service.verify_otp(body.challenge_id, body.code)
        except AuthError as exc:
            raise error(401, exc.code) from None
        runtime.demo_codes.pop(body.challenge_id, None)
    expires_in = max(0, int((grant.session.expires_at - runtime.clock()).total_seconds()))
    return {"session_token": grant.token, "expires_at": grant.session.expires_at, "expires_in": expires_in,
            "customer_ref": grant.session.customer_ref}


def logout(runtime: Runtime, token: str, error: type) -> None:
    with runtime.lock:
        try:
            runtime.service.identity.revoke(token)
        except AuthError as exc:
            raise error(401, exc.code) from None


def _options(options) -> list[dict[str, Any]]:
    return [{"index": o.index, "kind": o.kind, "label": o.label, "charge": o.charge, "reasons": list(o.reasons)}
            for o in options]


def turn_response(result: TurnResult, error: type) -> dict[str, Any]:
    if result.error:
        status = 401 if result.error in SESSION_CODES else TURN_ERRORS.get(result.error, 400)
        raise error(status, result.error, result.trace_id)
    handoff = result.handoff or {}
    return {
        "conversation_id": result.conversation_id, "trace_id": result.trace_id, "language": result.language,
        "stage": result.stage, "reply": result.reply, "reply_source": result.reply_source,
        "options": _options(result.options), "recognition": result.recognition,
        "confirmation": result.confirmation, "case": result.case,
        "handoff_id": handoff.get("handoff_id"),
        "transfer_reason": (handoff.get("transfer_reason") or {}).get("code"),
        "trail": [s.as_dict() for s in result.trail], "llm": result.llm, "latency_ms": result.latency_ms,
    }


def _customer(runtime: Runtime, token: str, error: type) -> str:
    try:
        return runtime.service.identity.validate(token).customer_id
    except AuthError as exc:
        raise error(401, exc.code) from None


def conversation(runtime: Runtime, token: str, conversation_id: str, error: type) -> dict[str, Any]:
    with runtime.lock:
        state = runtime.orchestrator.store.get(conversation_id, _customer(runtime, token, error))
        if state is None:
            raise error(404, "conversation_not_found")
        return {
            "conversation_id": state.conversation_id, "language": state.language, "stage": state.stage,
            "transcript": [{"role": role, "text": text} for role, text in state.transcript],
            "options": _options(state.options), "recognition": recognition_view(state),
            "confirmation": confirmation_view(state),
            "case_id": state.case_id, "handoff_id": (state.handoff or {}).get("handoff_id"),
        }


def translate(runtime: Runtime, token: str, conversation_id: str, body: Any, error: type) -> dict[str, Any]:
    """English rendering of one message of the caller's own conversation, for reviewers.

    The text must be a whole transcript message of that role, so the route cannot translate arbitrary input or be
    driven through many substrings of one long message. A cached message answers without the model, even when no
    model is configured. A model call counts against a per-session quota and a global limit (settings), and runs
    outside the runtime lock: the LLM port writes to the audit log, which locks itself, and every other visitor's
    turn keeps moving while the provider answers. Concurrent requests for the same message share one call.
    """
    settings = runtime.settings
    with runtime.lock:
        try:
            session = runtime.service.identity.validate(token)
        except AuthError as exc:
            raise error(401, exc.code) from None
        state = runtime.orchestrator.store.get(conversation_id, session.customer_id)
        if state is None:
            raise error(404, "conversation_not_found")
        if (body.role, body.text) not in state.transcript:
            raise error(404, "not_found")
        key = (conversation_id, body.role, hashlib.sha256(body.text.encode("utf-8")).hexdigest())
        if key in runtime.translations:
            return {**runtime.translations[key], "cached": True}
        choice = runtime.llm_choice
        if choice.llm is None:
            raise error(503, "translation_unavailable")
        waiter = runtime.translating.get(key)
        if waiter is None:
            used = runtime.translate_used.get(session.session_id, 0)
            if used >= settings.translate_per_session or not runtime.translate_limit.allow("translate"):
                raise error(429, "rate_limited")
            runtime.translate_used[session.session_id] = used + 1
            runtime.translating[key] = threading.Event()
        names, language, trace_id = runtime.service.customer_names(session), state.language, new_trace_id()
        runtime.stack.audit.bind(trace_id, conversation_id)
    if waiter is not None:  # another request is already paying for this message
        waiter.wait(TRANSLATE_WAIT_S)
        with runtime.lock:
            hit = runtime.translations.get(key)
        if hit is None:
            raise error(503, "translation_unavailable")
        return {**hit, "cached": True}
    try:
        text = choice.llm.translate(body.text, language, known_names=names, trace_id=trace_id)
        if not text:
            raise error(503, "translation_unavailable", trace_id)
        result = {"translation": text, "source_language": language, "target_language": "en",
                  "machine_translation": True, "masked": mask_text(body.text, names) != body.text,
                  "provider": choice.provider, "model": choice.model}
        with runtime.lock:
            runtime.remember_translation(key, result)
    except LLMUnavailable:  # provider failure or daily budget reached (BudgetExceeded arrives wrapped)
        raise error(503, "translation_unavailable", trace_id) from None
    finally:
        with runtime.lock:
            runtime.translating.pop(key).set()
    return {**result, "cached": False}


def case_status(runtime: Runtime, token: str, case_id: str, error: type) -> dict[str, Any]:
    with runtime.lock:
        result = runtime.service.execute("get_case_status", {"case_id": case_id}, token,
                                         "rq_" + secrets.token_hex(10))
    if result.ok:
        return {k: result.data[k] for k in ("case_id", "transaction_id", "status", "created_at", "policy_rule_ids")}
    code = result.error.code if result.error else "not_found"
    if code in SESSION_CODES:
        raise error(401, code, result.trace_id)
    raise error(404, "not_found", result.trace_id)  # foreign, missing or malformed ids look the same


def queue_item(item: QueueItem) -> dict[str, Any]:
    return {"conversation_id": item.conversation_id, "received_at": item.received_at, "status": "pending",
            "handoff": item.handoff}


def _chain(runtime: Runtime) -> dict[str, Any]:
    """Chain status of the stored audit files (or process memory without an audit directory). Called outside the
    runtime lock: the audit log caches its result per file, so only files that changed are read again."""
    check = runtime.stack.audit.check_stored()
    return {"status": "intact" if check.intact else "broken", "checked_at": runtime.clock(),
            "records_checked": check.records_checked, "source": check.source, "files_checked": check.files_checked,
            "first_bad_seq": check.first_bad_seq}


def conversations(runtime: Runtime) -> list[dict[str, Any]]:
    """The audit index: every conversation of this process, resolved ones included."""
    with runtime.lock:
        states = runtime.conversations()
        rows = [(s, list(s.trace_ids), s.handoff or {}) for s in states]
    counts: dict[str, int] = {}
    for record in runtime.stack.audit.records():
        if record.conversation_id:
            counts[record.conversation_id] = counts.get(record.conversation_id, 0) + 1
    return [{"conversation_id": s.conversation_id, "created_at": s.created_at, "language": s.language,
             "stage": s.stage, "turns": len(traces), "trace_ids": traces, "records": counts.get(s.conversation_id, 0),
             "case_id": s.case_id, "handoff_id": handoff.get("handoff_id"),
             "transfer_reason": (handoff.get("transfer_reason") or {}).get("code")}
            for s, traces, handoff in rows]


def conversation_audit(runtime: Runtime, conversation_id: str, error: type) -> dict[str, Any]:
    with runtime.lock:
        state = runtime.orchestrator.store.get_any(conversation_id)
        if state is None:
            raise error(404, "conversation_not_found")
        trace_ids, trail = list(state.trace_ids), [s.as_dict() for s in state.trail]
    records = [r.model_dump() for r in runtime.stack.audit.records(conversation_id=conversation_id)]
    return {"conversation_id": conversation_id, "trace_ids": trace_ids, "trail": trail, "records": records,
            "chain": _chain(runtime)}


def trace(runtime: Runtime, trace_id: str, error: type) -> dict[str, Any]:
    records = runtime.stack.audit.records(trace_id)
    if not records:
        raise error(404, "not_found")
    conversation_id = next((r.conversation_id for r in records if r.conversation_id), None)
    trace_ids: list[str] = []
    if conversation_id:
        with runtime.lock:
            state = runtime.orchestrator.store.get_any(conversation_id)
            trace_ids = list(state.trace_ids) if state is not None else []
    return {"trace_id": trace_id, "conversation_id": conversation_id, "conversation_trace_ids": trace_ids,
            "records": [r.model_dump() for r in records], "chain": _chain(runtime)}
