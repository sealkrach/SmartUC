"""In-memory sliding-window limiters: login brute-force protection and per-user AI call quotas.

Process-local by design (single backend instance). For a multi-replica deployment move the counters to
Redis with the same interface; the call sites do not change.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._events[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def hit(self, key: str) -> bool:
        """Register an event; returns False when the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def count(self, key: str) -> int:
        with self._lock:
            return len(self._prune(key, time.monotonic()))

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


class LoginGuard:
    """Counts failed logins per account and per client IP; locks out after `max_failures`."""

    def __init__(self, max_failures: int, window_seconds: int, lockout_seconds: int):
        self.failures = SlidingWindow(max_failures, window_seconds)
        self.lockout_seconds = lockout_seconds
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                until = self._locked_until.get(k)
                if until and until > now:
                    return True
                if until:
                    del self._locked_until[k]
        return False

    def register_failure(self, *keys: str) -> bool:
        """Returns True when this failure triggered a lockout (reached `max_failures` within the window)."""
        locked = False
        for k in keys:
            self.failures.hit(k)
            if self.failures.count(k) >= self.failures.limit:
                with self._lock:
                    self._locked_until[k] = time.monotonic() + self.lockout_seconds
                locked = True
        return locked

    def register_success(self, *keys: str) -> None:
        for k in keys:
            self.failures.reset(k)
            with self._lock:
                self._locked_until.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._locked_until.clear()
        self.failures._events.clear()
