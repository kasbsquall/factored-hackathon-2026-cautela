"""Daily cap on LLM calls and estimated spend, so a public demo cannot run up a provider bill.

BudgetedAdapter wraps any LLMAdapter. Before each call it checks today's counters (UTC day, wall clock, not the
service clock). When the call cap or the USD cap is reached it raises BudgetExceeded without contacting the
provider. MaskedLLM turns that into LLMUnavailable, and the orchestrator takes the path it already takes on a
provider outage: the deterministic parser for understanding and the reply templates for wording. The service keeps
answering, and the trail records the fallback. `status()` says which mode is active, for GET /health.

  LLM_DAILY_MAX_CALLS  calls per UTC day, default 2000. A call is counted when it starts, so the cap is exact.
  LLM_DAILY_MAX_USD    estimated USD per UTC day, default 1.00
  LLM_BUDGET_FILE      JSON file that keeps today's counters across restarts; without it they live in memory

Limits, stated rather than hidden:
  - The USD figure is an estimate from agent/llm/prices.yaml and the token counts the provider returns. It is not
    the provider's invoice. A call with no known price or token count adds nothing to it and is counted in
    `unpriced_calls` instead.
  - The check runs before a call and the cost is added after it, so the last call of a day can overshoot the USD
    cap by the cost of one call (with max_tokens=512 on gpt-6-luna, a fraction of a cent).
  - One process. Two processes sharing one file would overwrite each other's counters.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from agent.llm.port import Completion, LLMAdapter, MaskedPrompt
from agent.llm.pricing import PriceTable

log = logging.getLogger("cautela.llm.budget")

DEFAULT_MAX_CALLS = 2000
DEFAULT_MAX_USD = 1.00


class BudgetExceeded(RuntimeError):
    """Today's cap is reached. No provider call was made."""


class BudgetConfigError(ValueError):
    """A budget variable is not a positive number. Raised at startup so the service never runs uncapped."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BudgetLimits:
    max_calls: int = DEFAULT_MAX_CALLS
    max_usd: float = DEFAULT_MAX_USD
    state_file: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BudgetLimits:
        def positive(name: str, cast: Callable[[str], float], default: float) -> Any:
            raw = (environ.get(name) or "").strip()
            if not raw:
                return default
            try:
                value = cast(raw)
            except ValueError as exc:
                raise BudgetConfigError(f"{name} must be a number") from exc
            if not math.isfinite(value) or value <= 0:
                raise BudgetConfigError(f"{name} must be positive")
            return value

        path = (environ.get("LLM_BUDGET_FILE") or "").strip()
        return cls(positive("LLM_DAILY_MAX_CALLS", int, DEFAULT_MAX_CALLS),
                   positive("LLM_DAILY_MAX_USD", float, DEFAULT_MAX_USD), Path(path) if path else None)


class DailyBudget:
    """Counters for one UTC day: calls started, estimated USD, calls with no known cost, calls refused."""

    def __init__(self, limits: BudgetLimits, now: Callable[[], datetime] = _utc_now) -> None:
        self.limits = limits
        self._now = now
        self._lock = threading.Lock()
        self._warned_day: date | None = None
        self._day = now().date()
        self._counts = self._load()

    def reserve(self) -> None:
        """Count one call, or raise BudgetExceeded when today's cap is already reached."""
        with self._lock:
            self._roll()
            reason = self._exhausted_reason()
            if reason:
                self._counts["refused"] += 1
                self._save()
                if self._warned_day != self._day:
                    self._warned_day = self._day
                    log.warning("LLM daily budget reached (%s): deterministic parser and templates until %s",
                                reason, self._resets_at().isoformat())
                raise BudgetExceeded(reason)
            self._counts["calls"] += 1
            self._save()

    def charge(self, usd: float | None) -> None:
        """Add the estimated cost of a finished call; None means the cost is not known."""
        with self._lock:
            self._roll()
            if usd is None:
                self._counts["unpriced_calls"] += 1
            else:
                self._counts["usd"] = round(self._counts["usd"] + usd, 8)
            self._save()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._roll()
            reason = self._exhausted_reason()
            return {"enabled": True, "mode": "deterministic_fallback" if reason else "llm",
                    "exhausted_reason": reason, "day": self._day.isoformat(),
                    "calls": self._counts["calls"], "max_calls": self.limits.max_calls,
                    "usd_estimated": round(self._counts["usd"], 6), "max_usd": self.limits.max_usd,
                    "unpriced_calls": self._counts["unpriced_calls"], "refused_calls": self._counts["refused"],
                    "resets_at": self._resets_at().isoformat()}

    # ---- internals (called with the lock held) -----------------------------------------------------------
    def _exhausted_reason(self) -> str | None:
        if self._counts["calls"] >= self.limits.max_calls:
            return "max_calls"
        if self._counts["usd"] >= self.limits.max_usd:
            return "max_usd"
        return None

    def _resets_at(self) -> datetime:
        return datetime.combine(self._day + timedelta(days=1), time(0), tzinfo=UTC)

    def _roll(self) -> None:
        today = self._now().date()
        if today != self._day:
            self._day, self._counts = today, self._zero()

    @staticmethod
    def _zero() -> dict[str, Any]:
        return {"calls": 0, "usd": 0.0, "unpriced_calls": 0, "refused": 0}

    def _load(self) -> dict[str, Any]:
        path = self.limits.state_file
        if path is None or not path.is_file():
            return self._zero()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("day") != self._day.isoformat():
                return self._zero()
            return {"calls": int(data["calls"]), "usd": float(data["usd"]),
                    "unpriced_calls": int(data["unpriced_calls"]), "refused": int(data["refused"])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # A damaged file can at most grant one extra day's cap; say so instead of refusing to start.
            log.warning("LLM budget file unreadable: counting today from zero")
            return self._zero()

    def _save(self) -> None:
        path = self.limits.state_file
        if path is None:
            return
        payload = json.dumps({"day": self._day.isoformat(), **self._counts})
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".budget-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, path)
        except OSError:
            # The cap is still enforced in memory for this process; only persistence across a restart is lost.
            log.warning("LLM budget file not writable: counters kept in memory only")


class BudgetedAdapter:
    """LLMAdapter that asks a DailyBudget before every call and reports the cost after it."""

    def __init__(self, inner: LLMAdapter, budget: DailyBudget, prices: PriceTable | None = None) -> None:
        self.inner = inner
        self.budget = budget
        self.prices = prices or PriceTable.load()

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def __repr__(self) -> str:
        return f"BudgetedAdapter({self.inner!r}, {self.budget.limits!r})"

    def complete(self, prompt: MaskedPrompt, max_tokens: int) -> Completion:
        self.budget.reserve()
        try:
            completion = self.inner.complete(prompt, max_tokens)
        except Exception:
            self.budget.charge(None)  # the provider may have billed part of it; the cost is not known
            raise
        cost, _ = self.prices.cost(self.provider, completion.model, completion.input_tokens,
                                   completion.output_tokens)
        self.budget.charge(cost)
        return completion


def with_budget(adapter: LLMAdapter, environ: Mapping[str, str] | None = None) -> BudgetedAdapter:
    """Wrap an adapter with the limits from LLM_DAILY_MAX_CALLS, LLM_DAILY_MAX_USD and LLM_BUDGET_FILE."""
    limits = BudgetLimits.from_env(os.environ if environ is None else environ)
    return BudgetedAdapter(adapter, DailyBudget(limits))


def budget_status(llm: object) -> dict[str, Any]:
    """Budget state of a MaskedLLM (or None) for a health endpoint."""
    adapter = getattr(llm, "adapter", None)
    if isinstance(adapter, BudgetedAdapter):
        return adapter.budget.status()
    reason = "no model configured" if llm is None else "adapter not wrapped"
    return {"enabled": False, "mode": "deterministic" if llm is None else "llm", "note": reason}
