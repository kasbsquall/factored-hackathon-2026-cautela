"""Fixed-size sliding-window rate limiter, in process memory, keyed by endpoint group and client address.

A single-process prototype limit. Behind a proxy the client address must come from a trusted forwarding
header, and a multi-process deployment needs a shared store (for example Redis); both are deployment work.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        self.limit, self.window_s, self._clock, self._max_keys = limit, window_s, clock, max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            if len(self._hits) > self._max_keys:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_s]:
            del self._hits[key]
