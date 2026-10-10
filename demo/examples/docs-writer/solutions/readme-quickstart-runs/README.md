# shelfcache

shelfcache provides small in-process caches for Python: a `TTLCache` mapping with least recently used
eviction and an optional time to live per entry, a `@cached` decorator that memoizes function results in
one, and `dump()` / `load()` to persist a cache to JSON and restore it later.

## Installation

```
pip install shelfcache
```

Requires Python 3.10 or newer. There are no dependencies.

## Quickstart

```python
from shelfcache import TTLCache, cached

# A cache with room for two entries; entries live 60 seconds unless told otherwise.
cache = TTLCache(maxsize=2, ttl=60)
cache.set("session:alice", {"cart": 3})
cache.set("session:bob", {"cart": 1}, ttl=5)  # this entry expires after 5 seconds
print(cache.get("session:alice"))             # {'cart': 3}, and alice is now most recently used

cache.set("session:carol", {"cart": 7})       # the cache is full: bob, least recently used, is evicted
print("session:bob" in cache)                 # False
print(cache.stats())                          # CacheStats(hits=1, misses=0, evictions=1, expirations=0)


@cached(maxsize=256, ttl=30)
def exchange_rate(currency):
    print(f"  looking up {currency}...")
    return {"EUR": 0.92, "JPY": 151.3}[currency]


print(exchange_rate("EUR"))  # computed
print(exchange_rate("EUR"))  # served from the cache
print(exchange_rate.cache.stats().hits)  # 1
```

## API overview

| Name | What it does | Defined in |
|---|---|---|
| `TTLCache(maxsize=128, ttl=None, *, clock=time.monotonic)` | The cache. `get`, `set(key, value, *, ttl=None)`, `delete`, `pop`, `clear` and the mapping operations | `shelfcache/cache.py` |
| `TTLCache.purge()` | Removes every expired entry, returns how many | `shelfcache/cache.py` |
| `TTLCache.remaining_ttl(key)` | Seconds before an entry expires, or None | `shelfcache/cache.py` |
| `TTLCache.stats()` / `CacheStats` | Hit, miss, eviction and expiration counters as a named tuple | `shelfcache/cache.py` |
| `cached(maxsize=128, *, ttl=None, key=None)` | Memoizing decorator; the wrapper has `.cache` and `.cache_clear()` | `shelfcache/decorators.py` |
| `dump(cache, path, *, include_expired=False)` / `load(path, *, maxsize=None, clock=...)` | Save to and restore from JSON, keeping remaining time to live | `shelfcache/serialize.py` |

See [CHANGELOG.md](CHANGELOG.md) for changes between versions.
