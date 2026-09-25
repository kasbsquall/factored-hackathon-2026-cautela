"""Injectable clock. Every time-dependent control takes a Clock so tests can move time without sleeping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


class FrozenClock:
    """A clock that only moves when told to. Used by tests and by offline evaluation runs."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)
