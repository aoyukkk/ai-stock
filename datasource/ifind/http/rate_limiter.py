from __future__ import annotations

import time
from collections.abc import Callable


class IFindHttpCallLimitReached(RuntimeError):
    pass


class IFindHttpSerialRateLimiter:
    def __init__(
        self,
        maximum: int = 30,
        interval_ms: int = 1000,
        *,
        authorized_maximum: int = 30,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # The default remains 30.  Higher ceilings are accepted only when a
        # caller explicitly supplies a run-scoped authorization.
        if authorized_maximum < 1 or authorized_maximum > 400:
            raise ValueError("IFIND_HTTP_AUTHORIZED_LIMIT_INVALID")
        if maximum < 1 or maximum > authorized_maximum:
            raise ValueError("IFIND_HTTP_CALL_LIMIT_INVALID")
        if interval_ms < 1000:
            raise ValueError("IFIND_HTTP_INTERVAL_TOO_SHORT")
        self.maximum = maximum
        self.interval_ms = interval_ms
        self.sleep = sleep
        self.call_count = 0
        self._last_call_at: float | None = None

    def wait(self) -> None:
        if self.call_count >= self.maximum:
            raise IFindHttpCallLimitReached("IFIND_HTTP_CALL_LIMIT_REACHED")
        now = time.monotonic()
        if self._last_call_at is not None:
            remaining = self.interval_ms / 1000 - (now - self._last_call_at)
            if remaining > 0:
                self.sleep(remaining)

    def record(self) -> None:
        self.call_count += 1
        self._last_call_at = time.monotonic()
