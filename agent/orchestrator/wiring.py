"""Assemble the service stack (identity, repository, case store, audit, ToolService) for the demo and the API.

Nothing here decides anything; it only connects the pieces that already enforce the controls. Secrets are passed
in by the caller and never printed.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent.clock import Clock
from agent.security.audit import AuditLog
from agent.security.session import MAX_CHALLENGES_PER_WINDOW, IdentityService, MockChannel
from agent.service import ToolService
from agent.tools.faults import FaultInjector
from agent.tools.repository import CaseStore, WarehouseRepository

MIN_SECRET_BYTES = 32


@dataclass
class Stack:
    service: ToolService
    identity: IdentityService
    repo: WarehouseRepository
    cases: CaseStore
    audit: AuditLog
    faults: FaultInjector
    channel: MockChannel

    def close(self) -> None:
        self.repo.close()
        self.cases.close()


class OffsetClock:
    """Real time, shifted so that 'now' starts at `start`. Sessions and confirmations still expire in real time,
    while the static dataset's transactions stay inside their claim windows."""

    def __init__(self, start: datetime) -> None:
        start = start if start.tzinfo else start.replace(tzinfo=UTC)
        self._offset = start - datetime.now(UTC)

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self._offset


def session_secret(environ: dict[str, str] | None = None) -> tuple[bytes, bool]:
    """SESSION_SECRET from the environment, or a random per-process secret. Returns (secret, from_env)."""
    value = (environ if environ is not None else os.environ).get("SESSION_SECRET", "")
    if len(value.encode()) >= MIN_SECRET_BYTES:
        return value.encode(), True
    return secrets.token_bytes(48), False


def login_challenges_per_window() -> int:
    """Login codes per document per 15 minutes. 3 by default; the public demo raises it because its test
    identities are shared by every visitor and their codes are shown on screen, so there is nothing to guess."""
    raw = os.environ.get("CAUTELA_LOGIN_CHALLENGES", "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else MAX_CHALLENGES_PER_WINDOW


def build_stack(warehouse: str | Path, clock: Clock, secret: bytes, cases_db: str | Path = ":memory:",
                audit_dir: str | Path | None = None, faults: FaultInjector | None = None,
                sleep=None) -> Stack:
    faults = faults or FaultInjector()
    repo = WarehouseRepository(warehouse, faults)
    cases = CaseStore(cases_db, faults)
    channel = MockChannel()
    identity = IdentityService(secret, repo, channel, clock, max_challenges=login_challenges_per_window())
    audit = AuditLog(secret, audit_dir, clock)
    kwargs = {"sleep": sleep} if sleep is not None else {}
    service = ToolService(secret, identity, repo, cases, audit, clock, **kwargs)
    return Stack(service, identity, repo, cases, audit, faults, channel)
