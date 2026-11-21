"""Bounded in-process sliding windows; one backend process, no proxy header trust."""
import threading
import time
from collections import OrderedDict, deque


class RateLimiter:
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key, limit, window=60, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            # Expire only idle buckets. Never evict a live limiter to admit a new key.
            for old in list(self.buckets):
                values = self.buckets[old]
                while values and values[0] <= now - window:
                    values.popleft()
                if not values:
                    del self.buckets[old]
            if key not in self.buckets:
                if len(self.buckets) >= self.capacity:
                    return False
                self.buckets[key] = deque()
            values = self.buckets[key]
            if len(values) >= limit:
                return False
            values.append(now)
            return True
