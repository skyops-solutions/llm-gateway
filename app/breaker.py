"""Per-provider circuit breaker.

After `threshold` consecutive failures a provider is skipped for `cooldown` seconds, so a provider that is down
does not add retries and waiting to every request. After the cooldown one request is allowed through to test it.
State is per gateway process (replica), which is good enough: each replica learns from its own traffic.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class Breaker:
    def __init__(self, threshold: int = 5, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.cooldown = cooldown
        self.clock = clock
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        until = self._open_until.get(name)
        if until is None:
            return True
        if self.clock() >= until:
            # Half-open: let one request test the provider. A further failure re-opens it at once.
            del self._open_until[name]
            self._fails[name] = self.threshold - 1
            return True
        return False

    def success(self, name: str) -> None:
        self._fails.pop(name, None)
        self._open_until.pop(name, None)

    def failure(self, name: str) -> bool:
        """Record a failure. Returns True if this failure opened the breaker."""
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        if n >= self.threshold and name not in self._open_until:
            self._open_until[name] = self.clock() + self.cooldown
            return True
        return False

    def is_open(self, name: str) -> bool:
        return name in self._open_until
