"""The TTLCache class: a size bounded mapping with least recently used eviction and per entry expiry."""

from __future__ import annotations

import time
import warnings
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from typing import Any, NamedTuple

_MISSING = object()


class CacheStats(NamedTuple):
    """Counters for one cache since it was created or last cleared."""

    hits: int
    misses: int
    evictions: int
    expirations: int


class TTLCache:
    """A mapping that holds at most `maxsize` entries, each optionally expiring after `ttl` seconds.

    When the cache is full, setting a new key evicts the least recently used entry. Reading a key
    with `get` or `cache[key]` marks it as recently used. An expired entry is removed the next time
    it is read and counts as a miss, never as a hit.

    Args:
        maxsize: Maximum number of entries. Must be at least 1.
        ttl: Default time to live in seconds for new entries, or None for entries that never expire.
            Must be positive when given.
        clock: Function returning the current time in seconds. Defaults to `time.monotonic`; pass a
            fake clock in tests.

    Raises:
        ValueError: If `maxsize` is less than 1 or `ttl` is not positive.
    """

    def __init__(self, maxsize: int = 128, ttl: float | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be at least 1, got {maxsize}")
        if ttl is not None and ttl <= 0:
            raise ValueError(f"ttl must be positive, got {ttl}")
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._data: OrderedDict[Hashable, tuple[Any, float | None]] = OrderedDict()
        self._hits = self._misses = self._evictions = self._expirations = 0

    # ------------------------------------------------------------ reading

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return the value for `key`, or `default` if it is missing or expired."""
        entry = self._live_entry(key)
        if entry is _MISSING:
            self._misses += 1
            return default
        self._hits += 1
        self._data.move_to_end(key)
        return entry

    def __getitem__(self, key: Hashable) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return self._live_entry(key) is not _MISSING  # type: ignore[arg-type]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[Hashable]:
        return iter(list(self._data))

    # ------------------------------------------------------------ writing

    def set(self, key: Hashable, value: Any, *, ttl: float | None = None) -> None:
        """Store `value` under `key`, evicting the least recently used entry if the cache is full.

        Args:
            key: Any hashable key.
            value: The value to store.
            ttl: Time to live in seconds for this entry only, overriding the cache default.

        Raises:
            ValueError: If `ttl` is given and not positive.
        """
        if ttl is not None and ttl <= 0:
            raise ValueError(f"ttl must be positive, got {ttl}")
        ttl = self.ttl if ttl is None else ttl
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= self.maxsize:
            self._data.popitem(last=False)
            self._evictions += 1
        self._data[key] = (value, expires_at)

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self.set(key, value)

    def delete(self, key: Hashable) -> bool:
        """Remove `key`. Returns True if it was present (expired or not), False otherwise."""
        return self._data.pop(key, _MISSING) is not _MISSING

    def __delitem__(self, key: Hashable) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def pop(self, key: Hashable, default: Any = _MISSING) -> Any:
        entry = self._live_entry(key)
        self._data.pop(key, None)
        if entry is _MISSING:
            if default is _MISSING:
                raise KeyError(key)
            return default
        return entry

    def clear(self) -> None:
        self._data.clear()
        self._hits = self._misses = self._evictions = self._expirations = 0

    def purge(self) -> int:
        now = self._clock()
        dead = [k for k, (_, exp) in self._data.items() if exp is not None and exp <= now]
        for k in dead:
            del self._data[k]
        self._expirations += len(dead)
        return len(dead)

    def expire(self) -> int:
        """Deprecated alias of `purge`; will be removed in 0.6.0."""
        warnings.warn("TTLCache.expire() is deprecated, use purge()", DeprecationWarning, stacklevel=2)
        return self.purge()

    # ------------------------------------------------------------ introspection

    def stats(self) -> CacheStats:
        """Return hit, miss, eviction and expiration counters as a `CacheStats` named tuple."""
        return CacheStats(self._hits, self._misses, self._evictions, self._expirations)

    def remaining_ttl(self, key: Hashable) -> float | None:
        """Seconds until `key` expires, or None if it never expires.

        Raises:
            KeyError: If `key` is missing or already expired.
        """
        if self._live_entry(key) is _MISSING:
            raise KeyError(key)
        expires_at = self._data[key][1]
        return None if expires_at is None else max(0.0, expires_at - self._clock())

    def _live_entry(self, key: Hashable) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return _MISSING
        value, expires_at = entry
        if expires_at is not None and expires_at <= self._clock():
            del self._data[key]
            self._expirations += 1
            return _MISSING
        return value

    def __repr__(self) -> str:
        return f"TTLCache(maxsize={self.maxsize}, ttl={self.ttl}, len={len(self)})"
