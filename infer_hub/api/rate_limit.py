from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Simple in-memory RPM limiter per client key."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(1, rpm)
        self.window_sec = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window_sec:
            bucket.popleft()
        if len(bucket) >= self.rpm:
            return False
        bucket.append(now)
        return True
