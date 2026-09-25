"""Failure injection for evaluation, and bounded retries with exponential backoff.

Injection points (operation names):
  warehouse.read     tool reads of the source warehouse
  case_store.write   writes to the sandbox case store
  case_store.read    reads of the sandbox case store (used by the verify step)

Modes:
  timeout      raise ToolTimeout (transient, retried)
  error        raise ToolUnavailable (transient, retried)
  stale_read   the read returns the state before the latest write (the verify step must catch it)
  lost_write   the write reports success but nothing persists (the verify step must catch it)

A FaultSpec can fire a fixed number of times (`times`) and then heal, which is how a test shows that a retry
recovers, or fire forever, which is how a test shows the fallback to a handoff. Probabilistic faults use a seeded
random generator so evaluation runs are repeatable.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

T = TypeVar("T")


class FaultMode(StrEnum):
    TIMEOUT = "timeout"
    ERROR = "error"
    STALE_READ = "stale_read"
    LOST_WRITE = "lost_write"


class TransientToolError(Exception):
    """A failure worth retrying."""


class ToolTimeout(TransientToolError):
    pass


class ToolUnavailable(TransientToolError):
    pass


class RetriesExhausted(Exception):
    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"gave up after {attempts} attempts: {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


@dataclass
class FaultSpec:
    mode: FaultMode
    times: int | None = None  # None: fire on every call
    probability: float = 1.0


@dataclass
class FaultInjector:
    faults: dict[str, FaultSpec] = field(default_factory=dict)
    seed: int = 0
    fired: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def set(self, operation: str, mode: FaultMode | str, times: int | None = None, probability: float = 1.0) -> None:
        self.faults[operation] = FaultSpec(FaultMode(mode), times, probability)

    def clear(self) -> None:
        self.faults.clear()

    def check(self, operation: str) -> FaultMode | None:
        """Raise for transient modes; return stale_read or lost_write for the caller to apply."""
        spec = self.faults.get(operation)
        if spec is None or (spec.times is not None and self.fired.get(operation, 0) >= spec.times):
            return None
        if spec.probability < 1.0 and self._rng.random() >= spec.probability:
            return None
        self.fired[operation] = self.fired.get(operation, 0) + 1
        if spec.mode is FaultMode.TIMEOUT:
            raise ToolTimeout(f"injected timeout on {operation}")
        if spec.mode is FaultMode.ERROR:
            raise ToolUnavailable(f"injected error on {operation}")
        return spec.mode


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.05
    multiplier: float = 2.0
    max_delay_s: float = 0.5

    def delay(self, attempt: int) -> float:
        """Delay after the given failed attempt (1-based): 0.05, 0.10, 0.20 ... capped."""
        return min(self.base_delay_s * self.multiplier ** (attempt - 1), self.max_delay_s)


def call_with_retries(fn: Callable[[], T], policy: RetryPolicy,
                      sleep: Callable[[float], None] = time.sleep) -> tuple[T, int]:
    """Run fn, retrying transient errors up to policy.max_attempts. Returns (result, attempts used)."""
    last: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn(), attempt
        except TransientToolError as exc:
            last = exc
            if attempt < policy.max_attempts:
                sleep(policy.delay(attempt))
    raise RetriesExhausted(policy.max_attempts, last)
