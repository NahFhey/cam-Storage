"""Simple in-memory TTL cache for hot read paths (hot list, station counts)."""
import time
from typing import Any, Dict, Optional


class TTLCache:
    """In-memory cache with time-to-live expiry."""

    def __init__(self, default_ttl: int = 30):
        self._cache: Dict[str, dict] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry["expires"]:
            del self._cache[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl: int = None):
        self._cache[key] = {
            "value": value,
            "expires": time.monotonic() + (ttl or self._default_ttl)
        }

    def invalidate(self, key: str = None):
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)


# 30-second TTL suits shop floor refresh rates; writes invalidate everything
cache = TTLCache(default_ttl=30)
