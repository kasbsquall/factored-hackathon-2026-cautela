"""Append-only execution records with a trace id per request.

Each record holds the step, the tool, a keyed hash of the raw arguments, the arguments after PII masking, the
policy rule ids that fired, the outcome, the reason code and the latency. Raw arguments are never stored.
Records are chained (each carries the hash of the previous one), so an edited or deleted line breaks
`verify_chain()`. Storage is one JSONL file per UTC day plus an in-memory copy for the running process.

Retention: AUDIT_RETENTION_DAYS is a synthetic policy value. It mirrors the ten years the BCRA requires for the
complaint register (Proteccion de los Usuarios de Servicios Financieros, point 3.1.3) because the audit trail is
evidence for the same complaints. The legal value per country must be confirmed before deployment. Purging
removes whole expired day files and never edits a file in place.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.clock import Clock, system_clock
from agent.security.pii import mask_mapping
from agent.security.signing import Signer, canonical_json

AUDIT_RETENTION_DAYS = 3650  # synthetic policy, see module docstring
GENESIS_HASH = "0" * 64


class AuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    seq: int
    ts: str  # ISO 8601, kept as text so the hash chain is stable across serializations
    step: str
    tool: str | None
    args_hash: str | None
    masked_args: dict[str, Any]
    rule_ids: list[str]
    outcome: str
    reason: str | None
    latency_ms: float
    customer_ref: str | None
    attempts: int
    prev_hash: str
    record_hash: str


def new_trace_id() -> str:
    return "tr_" + secrets.token_hex(8)


def _hash_record(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(body))).hexdigest()


class AuditLog:
    def __init__(self, secret: bytes, directory: str | Path | None = None, clock: Clock = system_clock) -> None:
        self._hasher = Signer(secret, "audit")
        self._dir = Path(directory) if directory else None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._records: list[AuditRecord] = []

    def record(self, *, trace_id: str, step: str, outcome: str, tool: str | None = None,
               args: Mapping[str, Any] | None = None, rule_ids: Iterable[str] = (), reason: str | None = None,
               latency_ms: float = 0.0, customer_ref: str | None = None, attempts: int = 1,
               known_names: Iterable[str] = ()) -> AuditRecord:
        prev = self._records[-1].record_hash if self._records else GENESIS_HASH
        body = {
            "trace_id": trace_id, "seq": len(self._records), "ts": self._clock().isoformat(), "step": step,
            "tool": tool, "args_hash": self._hasher.digest(dict(args)) if args is not None else None,
            "masked_args": json.loads(canonical_json(mask_mapping(args or {}, known_names))),
            "rule_ids": sorted(set(rule_ids)),
            "outcome": outcome, "reason": reason, "latency_ms": round(latency_ms, 3), "customer_ref": customer_ref,
            "attempts": attempts, "prev_hash": prev,
        }
        record = AuditRecord(**body, record_hash=_hash_record(body))
        self._records.append(record)
        if self._dir:
            path = self._dir / f"audit-{record.ts[:10]}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")
        return record

    @contextmanager
    def timed(self) -> Iterator[dict[str, float]]:
        """Measure latency for a record: `with log.timed() as t: ...; t['ms']`."""
        box = {"ms": 0.0}
        start = time.perf_counter()
        try:
            yield box
        finally:
            box["ms"] = (time.perf_counter() - start) * 1000

    def records(self, trace_id: str | None = None) -> list[AuditRecord]:
        return [r for r in self._records if trace_id is None or r.trace_id == trace_id]

    def verify_chain(self, records: list[AuditRecord] | None = None) -> bool:
        prev = GENESIS_HASH
        for record in records if records is not None else self._records:
            body = record.model_dump(mode="json", exclude={"record_hash"})
            if record.prev_hash != prev or _hash_record(body) != record.record_hash:
                return False
            prev = record.record_hash
        return True

    @staticmethod
    def load(path: str | Path) -> list[AuditRecord]:
        return [AuditRecord(**json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def purge_expired(self, now: datetime | None = None) -> list[Path]:
        """Delete whole day files older than the retention window. Returns the files removed."""
        if not self._dir:
            return []
        cutoff = ((now or self._clock()) - timedelta(days=AUDIT_RETENTION_DAYS)).date()
        removed = []
        for path in sorted(self._dir.glob("audit-*.jsonl")):
            day = datetime.strptime(path.stem.removeprefix("audit-"), "%Y-%m-%d").date()
            if day < cutoff:
                path.unlink()
                removed.append(path)
        return removed
