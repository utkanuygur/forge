# shelfcache.cache

A size bounded mapping with least recently used (LRU) eviction and per entry expiry.

## Rules

- **Eviction:** setting a new key when the cache holds `maxsize` entries removes the least recently used
  entry. Overwriting an existing key never evicts.
- **Recency:** `get()`, `cache[key]` and `set()` mark the entry as most recently used. `key in cache`
  does not change recency.
- **Expiry:** an entry expires `ttl` seconds after it was set. An expired entry is removed lazily, when it
  is next read (`get`, `cache[key]`, `in`, `pop`, `remaining_ttl`) or by `purge()`. Reading an expired entry
  counts as a miss, never a hit.
- `len(cache)` counts stored entries, including expired ones that have not been removed yet. Call
  `purge()` first for an exact live count.

## `class TTLCache(maxsize: int = 128, ttl: float | None = None, *, clock: Callable[[], float] = time.monotonic)`

| Parameter | Meaning |
|---|---|
| `maxsize` | Maximum number of entries, at least 1. |
| `ttl` | Default time to live in seconds for new entries; `None` means entries never expire. Must be positive. |
| `clock` | Keyword only. Function returning the current time in seconds; pass a fake clock in tests. |

Raises `ValueError` if `maxsize < 1` or `ttl` is not positive. The attributes `maxsize` and `ttl` hold the
constructor values.

### Methods

| Signature | Returns | Notes |
|---|---|---|
| `get(key, default=None)` | value or `default` | Counts a hit or a miss; marks the entry recently used. |
| `set(key, value, *, ttl=None)` | `None` | `ttl` (keyword only) overrides the cache default for this entry; `ValueError` if not positive. May evict the LRU entry. |
| `delete(key)` | `bool` | True if the key was stored (expired or not). |
| `pop(key[, default])` | value | Removes and returns a live entry; returns `default` if missing or expired, or raises `KeyError` when no default is given. |
| `clear()` | `None` | Removes every entry and resets the stats counters to zero. |
| `purge()` | `int` | Removes every expired entry; returns how many were removed. |
| `expire()` | `int` | Deprecated alias of `purge()`; emits `DeprecationWarning`, removed in 0.6.0. |
| `stats()` | `CacheStats` | Counters since creation or the last `clear()`. |
| `remaining_ttl(key)` | `float` or `None` | Seconds left, `None` if the entry never expires; `KeyError` if missing or expired. |

### Mapping operations

| Operation | Behavior |
|---|---|
| `cache[key]` | Like `get`, but raises `KeyError` when missing or expired. |
| `cache[key] = value` | Same as `set(key, value)` with the default ttl. |
| `del cache[key]` | Removes the key; `KeyError` if it is not stored. |
| `key in cache` | True for a live entry; removes the entry if it expired. |
| `len(cache)`, `iter(cache)` | Stored entries, least recently used first. |

## `class CacheStats(NamedTuple)`

Fields: `hits`, `misses`, `evictions`, `expirations` (all `int`). Use `stats()._asdict()` for a dict.

## Example

```python
from shelfcache import TTLCache


class FakeClock:
    now = 0.0

    def __call__(self):
        return self.now


clock = FakeClock()
cache = TTLCache(maxsize=2, ttl=10, clock=clock)
cache["a"] = 1
cache.set("b", 2, ttl=1)
clock.now = 5
assert cache.get("b") is None          # expired: a miss
assert cache.remaining_ttl("a") == 5.0
print(cache.stats())                   # CacheStats(hits=0, misses=1, evictions=0, expirations=1)
```
