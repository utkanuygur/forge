# Changelog

All notable changes to shelfcache. The format follows Keep a Changelog and the project uses
semantic versioning (before 1.0, minor versions may break the API).

## Unreleased

### Changed
- **Breaking:** `cached()` now takes `ttl` as a keyword only argument. `@cached(256, 30)` must become
  `@cached(256, ttl=30)`. (#41)
- **Breaking:** `TTLCache.stats()` returns a `CacheStats` named tuple instead of a dict. Use
  `stats().hits` instead of `stats()["hits"]`; `stats()._asdict()` gives the old dict. (#44)

### Added
- `TTLCache.set()` accepts a per entry `ttl` that overrides the cache default. (#38)
- `TTLCache.purge()` removes every expired entry at once and returns how many it removed. (#39)
- `TTLCache.remaining_ttl(key)` reports the seconds left before an entry expires. (#45)
- `cached()` accepts a `key` function to build cache keys from unhashable arguments. (#42)
- `CacheStats` now also counts `expirations`. (#44)

### Deprecated
- `TTLCache.expire()` is now an alias of `purge()` that emits a `DeprecationWarning`; it will be
  removed in 0.6.0. (#39)

### Fixed
- Reading an expired entry with `get()` counted as a hit instead of a miss. (#40)
- `dump()` no longer leaves a half written file behind when serialization fails midway; the file is
  now replaced atomically. (#43)

## 0.4.0 - 2026-06-02

### Added
- `dump()` and `load()` save a cache to JSON and restore it with each entry's remaining time to live.
- `TTLCache` accepts a `clock` function for deterministic tests.

### Fixed
- `cached()` wrappers keep the wrapped function's `__name__` and docstring.

## 0.3.1 - 2026-04-18

### Fixed
- `TTLCache.__iter__` raised `RuntimeError` if an entry expired during iteration.

## 0.3.0 - 2026-03-27

### Added
- `TTLCache.stats()` with hit, miss and eviction counters.
- `cached()` wrappers expose `cache_clear()`.

## 0.2.0 - 2026-02-09

### Added
- The `cached()` decorator.

## 0.1.0 - 2026-01-15

- First release: `TTLCache` with least recently used eviction and a default time to live.
