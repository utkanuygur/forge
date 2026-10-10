# shelfcache 0.5.0 release notes

Upgrading from 0.4: two changes may break your code. Fix them first.

## Breaking changes

### `cached()` takes `ttl` as a keyword only argument (#41)

```python
# 0.4
@cached(256, 30)
def lookup(key): ...

# 0.5
@cached(256, ttl=30)
def lookup(key): ...
```

### `TTLCache.stats()` returns a `CacheStats` named tuple (#44)

```python
# 0.4
hits = cache.stats()["hits"]

# 0.5
hits = cache.stats().hits
as_dict = cache.stats()._asdict()   # the old dict shape, if you need it
```

## New features

- `TTLCache.set(key, value, *, ttl=...)`: a per entry time to live that overrides the cache default (#38).
- `TTLCache.purge()`: removes every expired entry at once and returns how many it removed (#39).
- `TTLCache.remaining_ttl(key)`: seconds left before an entry expires (#45).
- `cached(key=...)`: a function that builds cache keys, so you can memoize calls with unhashable
  arguments (#42).
- `CacheStats` also counts `expirations` (#44).

## Deprecations

- `TTLCache.expire()` is now an alias of `purge()` that emits a `DeprecationWarning`. It will be removed
  in 0.6.0: switch to `purge()` (#39).

## Fixes

- Reading an expired entry with `get()` counted as a hit; it now counts as a miss (#40).
- `dump()` replaces the file atomically, so a failure midway no longer leaves a half written file (#43).
