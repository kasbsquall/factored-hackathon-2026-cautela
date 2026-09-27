"""Append-only execution records with a trace id per request and a conversation id per conversation.

Each record holds the step, the tool, a keyed hash of the raw arguments, the arguments after PII masking, the
policy rule ids that fired, the outcome, the reason code and the latency. Raw arguments are never stored.
Records are chained (each carries the hash of the previous one), so an edited or deleted line breaks the chain.
Storage is one JSONL file per UTC day plus an in-memory copy for the running process. The chain runs across day
files and across restarts: a new process continues from the last stored record instead of starting at genesis.

Conversation id: every turn of a conversation gets its own trace id. The orchestrator binds each trace to its
conversation (`bind`), and every record written under that trace, whoever writes it (orchestrator step, tool
service, LLM port), carries the conversation id. Records outside a conversation (login) have none. The field is
part of the hashed body when present and absent from it otherwise, so lines written before it existed still verify.

Verification. `verify_chain()` walks the in-memory copy. `check_stored()` reads the day files on disk, which is
what can detect an edited or deleted line; the result is cached per file (name, size, modification time), so only a
file that changed is read again. After a retention purge the check starts at the oldest kept record. The chain is
unkeyed: it shows that a line changed, and it cannot stop someone who rewrites every later line (see SECURITY.md).

Retention: AUDIT_RETENTION_DAYS is a synthetic policy value. It mirrors the ten years the BCRA requires for the
complaint register (Proteccion de los Usuarios de Servicios Financieros, point 3.1.3) because the audit trail is
evidence for the same complaints. The legal value per country must be confirmed before deployment. Purging
removes whole expired day files and never edits a file in place. It runs when the log starts and again on the
first record of each new UTC day.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.clock import Clock, system_clock
from agent.security.pii import mask_mapping
from agent.security.signing import Signer, canonical_json

AUDIT_RETENTION_DAYS = 3650  # synthetic policy, see module docstring
GENESIS_HASH = "0" * 64
FILE_GLOB = "audit-*.jsonl"

log = logging.getLogger("cautela.audit")


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
    conversation_id: str | None = None


@dataclass(frozen=True)
class ChainCheck:
    """What a verification covered. `source` says whether the stored files or only process memory were read."""

    intact: bool
    records_checked: int
    source: Literal["stored_files", "memory"]
    files_checked: int = 0
    first_bad_seq: int | None = None


@dataclass(frozen=True)
class _FileCheck:
    stamp: tuple[int, int]  # size, modification time in ns
    entry_hash: str  # the hash this file's first record points to
    last_hash: str
    records: int
    first_bad_seq: int | None


def new_trace_id() -> str:
    return "tr_" + secrets.token_hex(8)


def _hash_record(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(body))).hexdigest()


def _body(record: AuditRecord) -> dict[str, Any]:
    body = record.model_dump(mode="json", exclude={"record_hash"})
    if body.get("conversation_id") is None:
        body.pop("conversation_id", None)
    return body


def _intact(record: AuditRecord, prev: str) -> bool:
    return record.prev_hash == prev and _hash_record(_body(record)) == record.record_hash


class AuditLog:
    def __init__(self, secret: bytes, directory: str | Path | None = None, clock: Clock = system_clock) -> None:
        self._hasher = Signer(secret, "audit")
        self._dir = Path(directory) if directory else None
        self._clock = clock
        self._records: list[AuditRecord] = []
        self._lock = threading.RLock()  # appends stay atomic when a caller holds no lock of its own
        self._conversations: dict[str, str] = {}
        self._anchor = GENESIS_HASH  # the hash the first in-memory record points to
        self._next_seq = 0
        self._purged_day: str | None = None
        self._file_checks: dict[str, _FileCheck] = {}
        self._last_check: tuple[tuple[tuple[str, int, int], ...], ChainCheck] | None = None
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
            self.purge_expired()
            self._purged_day = self._clock().date().isoformat()
            self._resume()

    def bind(self, trace_id: str, conversation_id: str) -> None:
        """Stamp every later record of this trace with the conversation id."""
        with self._lock:
            self._conversations[trace_id] = conversation_id

    def record(self, *, trace_id: str, step: str, outcome: str, tool: str | None = None,
               args: Mapping[str, Any] | None = None, rule_ids: Iterable[str] = (), reason: str | None = None,
               latency_ms: float = 0.0, customer_ref: str | None = None, attempts: int = 1,
               known_names: Iterable[str] = ()) -> AuditRecord:
        masked = json.loads(canonical_json(mask_mapping(args or {}, known_names)))
        args_hash = self._hasher.digest(dict(args)) if args is not None else None
        with self._lock:
            ts = self._clock().isoformat()
            if self._dir and ts[:10] != self._purged_day:
                self.purge_expired()
                self._purged_day = ts[:10]
            body = {
                "trace_id": trace_id, "seq": self._next_seq, "ts": ts, "step": step, "tool": tool,
                "args_hash": args_hash, "masked_args": masked, "rule_ids": sorted(set(rule_ids)),
                "outcome": outcome, "reason": reason, "latency_ms": round(latency_ms, 3),
                "customer_ref": customer_ref, "attempts": attempts,
                "prev_hash": self._records[-1].record_hash if self._records else self._anchor,
            }
            conversation_id = self._conversations.get(trace_id)
            if conversation_id is not None:
                body["conversation_id"] = conversation_id
            record = AuditRecord(**body, record_hash=_hash_record(body))
            if self._dir:
                line = record.model_dump_json(exclude={"conversation_id"} if conversation_id is None else None)
                with (self._dir / f"audit-{ts[:10]}.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            self._records.append(record)
            self._next_seq += 1
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

    def records(self, trace_id: str | None = None, conversation_id: str | None = None) -> list[AuditRecord]:
        with self._lock:
            snapshot = list(self._records)
        return [r for r in snapshot if (trace_id is None or r.trace_id == trace_id)
                and (conversation_id is None or r.conversation_id == conversation_id)]

    def verify_chain(self, records: list[AuditRecord] | None = None) -> bool:
        """Walk a list of records from genesis, or this process's in-memory copy from where it started."""
        if records is None:
            with self._lock:
                records, prev = list(self._records), self._anchor
        else:
            prev = GENESIS_HASH
        for record in records:
            if not _intact(record, prev):
                return False
            prev = record.record_hash
        return True

    def check_stored(self) -> ChainCheck:
        """Verify the chain as stored on disk, or the in-memory copy when this log has no directory."""
        if not self._dir:
            with self._lock:
                count = len(self._records)
            return ChainCheck(self.verify_chain(), count, "memory")
        with self._lock:  # sizes taken under the append lock, so only whole lines are read below
            key = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                        for p in sorted(self._dir.glob(FILE_GLOB)))
            if self._last_check is not None and self._last_check[0] == key:
                return self._last_check[1]
            known = dict(self._file_checks)
        checks: list[_FileCheck] = []
        prev: str | None = None
        for name, size, mtime in key:
            check = known.get(name)
            if check is None or check.stamp != (size, mtime) or (prev is not None and check.entry_hash != prev):
                check = self._check_file(self._dir / name, size, mtime, prev)
            known[name] = check
            checks.append(check)
            if check.first_bad_seq is not None:
                break
            prev = check.last_hash
        bad = next((c.first_bad_seq for c in checks if c.first_bad_seq is not None), None)
        result = ChainCheck(bad is None, sum(c.records for c in checks), "stored_files", len(checks), bad)
        with self._lock:
            self._file_checks = {name: known[name] for name, _, _ in key if name in known}
            self._last_check = (key, result)
        return result

    @staticmethod
    def _check_file(path: Path, size: int, mtime: int, prev: str | None) -> _FileCheck:
        """Verify one day file up to `size` bytes. `prev` is the hash its first record must point to; None marks
        the oldest kept file, which starts at genesis or, after a purge, wherever its first record points."""
        with path.open("rb") as handle:
            text = handle.read(size).decode("utf-8", errors="replace")
        entry, last_seq, count = prev, None, 0
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                record = AuditRecord(**json.loads(line))
            except (ValueError, TypeError, ValidationError):
                bad = 0 if last_seq is None else last_seq + 1
                return _FileCheck((size, mtime), entry or GENESIS_HASH, "", count, bad)
            if entry is None:
                entry = GENESIS_HASH if record.seq == 0 else record.prev_hash
            if not _intact(record, prev if prev is not None else entry):
                return _FileCheck((size, mtime), entry, "", count, record.seq)
            prev, last_seq, count = record.record_hash, record.seq, count + 1
        entry = entry if entry is not None else GENESIS_HASH
        return _FileCheck((size, mtime), entry, prev if prev is not None else entry, count, None)

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
        with self._lock:
            for path in sorted(self._dir.glob(FILE_GLOB)):
                try:
                    day = datetime.strptime(path.stem.removeprefix("audit-"), "%Y-%m-%d").date()
                except ValueError:
                    continue  # not a day file of this log
                if day < cutoff:
                    path.unlink()
                    removed.append(path)
        if removed:
            log.info("audit retention: %d expired day file(s) removed", len(removed))
        return removed

    def _resume(self) -> None:
        """Continue the stored chain: the next record points to the last stored one and takes the next seq."""
        for path in sorted(self._dir.glob(FILE_GLOB), reverse=True):
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not lines:
                continue
            try:
                last = AuditRecord(**json.loads(lines[-1]))
            except (ValueError, TypeError, ValidationError):
                log.warning("audit: the last stored record does not parse; the stored chain will verify as broken")
                return
            self._anchor, self._next_seq = last.record_hash, last.seq + 1
            return
