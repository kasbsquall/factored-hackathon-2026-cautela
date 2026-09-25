"""Build the structured handoff for a human agent and validate it against docs/schemas/handoff.schema.json.

The builder masks PII in every free-text field, trims the summary to the schema limit, and refuses to return a
document that does not validate, so a malformed handoff fails loudly in the service instead of reaching the
console. Facts must carry the tool and record they were read from; actions carry their verified status.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from agent.security.pii import mask_text

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "handoff.schema.json"
SUMMARY_MAX = 500


class HandoffValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Fact:
    fact: str
    source: str  # tool name and record id, e.g. "get_transaction:TX00000042"


@dataclass(frozen=True)
class ActionTaken:
    action: str
    status: str  # verified | failed | not_verified
    record_id: str | None = None


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_handoff(document: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(document), key=lambda e: list(e.path))
    messages = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors]
    try:
        datetime.fromisoformat(str(document.get("created_at", "")))
    except ValueError:
        messages.append("created_at: not an ISO 8601 date-time")
    if messages:
        raise HandoffValidationError(messages)


def build_handoff(*, trace_id: str, language: str, customer_ref: str, summary: str, intent: str,
                  reason_code: str, rule_ids: Iterable[str], created_at: datetime,
                  verified_facts: Sequence[Fact] = (), actions_taken: Sequence[ActionTaken] = (),
                  open_questions: Sequence[str] = (), evidence: Sequence[str] = (),
                  disputed_transaction_ids: Sequence[str] = (), confidence: float | None = None,
                  known_names: Iterable[str] = ()) -> dict[str, Any]:
    names = list(known_names)
    masked_summary = mask_text(summary, names)
    if len(masked_summary) > SUMMARY_MAX:
        masked_summary = masked_summary[:SUMMARY_MAX - 3].rstrip() + "..."
    request: dict[str, Any] = {"summary": masked_summary, "intent": intent}
    if disputed_transaction_ids:
        request["disputed_transaction_ids"] = list(disputed_transaction_ids)
    transfer: dict[str, Any] = {"code": reason_code, "rule_ids": sorted(set(rule_ids))}
    if confidence is not None:
        transfer["confidence"] = confidence
    document = {
        "handoff_id": "ho_" + secrets.token_hex(8), "trace_id": trace_id, "created_at": created_at.isoformat(),
        "language": language, "customer_ref": customer_ref, "request": request, "transfer_reason": transfer,
        "verified_facts": [{"fact": mask_text(f.fact, names), "source": f.source} for f in verified_facts],
        "actions_taken": [{k: v for k, v in {"action": a.action, "status": a.status, "record_id": a.record_id}.items()
                           if v is not None} for a in actions_taken],
        "evidence": [mask_text(e, names) for e in evidence],
        "open_questions": [mask_text(q, names) for q in open_questions],
    }
    validate_handoff(document)
    return document
