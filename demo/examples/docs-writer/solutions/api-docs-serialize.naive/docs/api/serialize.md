# shelfcache.serialize

## `dump(cache, path)`

Saves the cache to `path` as JSON and returns the number of entries. Raises `TypeError` for keys that are
not strings.

## `load(path, maxsize=None, clock=time.monotonic)`

Loads a `TTLCache` from a file written by `dump()`. Raises `ValueError` for unknown file versions.

## Example

```python
from shelfcache import load

cache = load("cache.json")
print(cache.get("user:1"))
```
