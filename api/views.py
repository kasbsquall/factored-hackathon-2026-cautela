"""Endpoint bodies that translate between the orchestrator or service and the API models.

Kept apart from api/app.py so the route table stays readable. Each function receives the Runtime and raises the
ApiError class it is given, so error codes stay stable and no internal detail reaches a response.
"""

from __future__ import annotations

import hashlib
import secrets
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

    The text must be part of a transcript line of that role, so the route cannot translate arbitrary input. The
    model call goes through the LLM port (masking, usage, audit without the text, and the daily budget when the
    adapter is wrapped) and runs under the runtime lock like a turn's calls, because the port writes to the shared
    audit chain. A cached message answers without the model, even when no model is configured.
    """
    with runtime.lock:
        try:
            session = runtime.service.identity.validate(token)
        except AuthError as exc:
            raise error(401, exc.code) from None
        state = runtime.orchestrator.store.get(conversation_id, session.customer_id)
        if state is None:
            raise error(404, "conversation_not_found")
        if not any(role == body.role and body.text in line for role, line in state.transcript):
            raise error(404, "not_found")
        key = (conversation_id, body.role, hashlib.sha256(body.text.encode("utf-8")).hexdigest())
        if key in runtime.translations:
            return {**runtime.translations[key], "cached": True}
        llm, names, trace_id = runtime.llm_choice.llm, runtime.service.customer_names(session), new_trace_id()
        if llm is None:
            raise error(503, "translation_unavailable")
        try:
            text = llm.translate(body.text, state.language, known_names=names, trace_id=trace_id)
        except LLMUnavailable:  # provider failure or daily budget reached (BudgetExceeded arrives wrapped)
            raise error(503, "translation_unavailable", trace_id) from None
        if not text:
            raise error(503, "translation_unavailable", trace_id)
        result = {"translation": text, "source_language": state.language, "target_language": "en",
                  "machine_translation": True, "masked": mask_text(body.text, names) != body.text,
                  "provider": runtime.llm_choice.provider, "model": runtime.llm_choice.model}
        runtime.remember_translation(key, result)
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
    records = runtime.stack.audit.records()
    return {"status": "intact" if runtime.stack.audit.verify_chain() else "broken",
            "checked_at": runtime.clock(), "records_checked": len(records)}


def conversation_audit(runtime: Runtime, conversation_id: str, error: type) -> dict[str, Any]:
    with runtime.lock:
        state = runtime.orchestrator.store.get_any(conversation_id)
        if state is None:
            raise error(404, "conversation_not_found")
        traces = set(state.trace_ids)
        records = [r.model_dump() for r in runtime.stack.audit.records() if r.trace_id in traces]
        return {"conversation_id": conversation_id, "trace_ids": list(state.trace_ids),
                "trail": [s.as_dict() for s in state.trail], "records": records, "chain": _chain(runtime)}


def trace(runtime: Runtime, trace_id: str, error: type) -> dict[str, Any]:
    with runtime.lock:
        records = [r.model_dump() for r in runtime.stack.audit.records(trace_id)]
        if not records:
            raise error(404, "not_found")
        return {"trace_id": trace_id, "records": records, "chain": _chain(runtime)}
