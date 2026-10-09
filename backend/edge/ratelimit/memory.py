"""In-process limiters. Fast and dependency free, but the state lives in ONE
process: with several gunicorn workers or servers each would enforce its own
separate limit. Use the Redis limiters when the limit must be shared.

Both take an injectable clock so behaviour over time can be tested without sleeping.
"""

import threading
import time
from collections import deque

from edge.lru import LRUCache

from .base import Decision


class InMemoryTokenBucket:
    """Tokens refill continuously at `refill_per_second` up to `capacity`.

    A request costs one token. Idle clients accumulate up to `capacity` tokens,
    which is what allows short bursts while still enforcing the average rate.
    """

    def __init__(self, capacity, refill_per_second, clock=time.time, max_keys=10_000):
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError('capacity must be >= 1 and refill rate > 0')
        self.capacity = capacity
        self.rate = refill_per_second
        self._clock = clock
        # Bounded: an attacker rotating IPs must not be able to exhaust memory.
        self._state = LRUCache(max_keys)
        self._lock = threading.Lock()

    def allow(self, key, cost=1, now=None):
        if cost < 1 or cost > self.capacity:
            raise ValueError('cost must be between 1 and the capacity')
        now = self._clock() if now is None else now
        with self._lock:
            tokens, last = self._state.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + max(0.0, now - last) * self.rate)
            last = max(last, now)
            if tokens >= cost:
                tokens -= cost
                allowed, retry_after = True, 0.0
            else:
                allowed, retry_after = False, (cost - tokens) / self.rate
            self._state.put(key, (tokens, last))
        return Decision(allowed, int(tokens), retry_after)


class InMemorySlidingWindow:
    """Exact sliding window log: remember each request time, count those in the last window."""

    def __init__(self, limit, window_seconds, clock=time.time, max_keys=10_000):
        if limit < 1 or window_seconds <= 0:
            raise ValueError('limit must be >= 1 and window > 0')
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._state = LRUCache(max_keys)
        self._lock = threading.Lock()

    def allow(self, key, cost=1, now=None):
        if cost < 1 or cost > self.limit:
            raise ValueError('cost must be between 1 and the limit')
        now = self._clock() if now is None else now
        with self._lock:
            log = self._state.get(key)
            if log is None:
                log = deque()
                self._state.put(key, log)
            while log and log[0] <= now - self.window:
                log.popleft()
            if len(log) + cost <= self.limit:
                log.extend([now] * cost)
                return Decision(True, self.limit - len(log), 0.0)
            # The slot that must expire for `cost` requests to fit:
            needed = len(log) + cost - self.limit
            retry_after = log[needed - 1] + self.window - now
            return Decision(False, max(0, self.limit - len(log)), max(0.0, retry_after))
