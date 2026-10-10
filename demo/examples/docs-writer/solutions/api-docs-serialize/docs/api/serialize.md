# shelfcache.serialize

Save a `TTLCache` to a JSON file and load it back. Each entry keeps the time to live it had left when it
was saved, and the least recently used order is preserved.

## `dump(cache, path, *, include_expired=False) -> int`

Writes `cache` to `path` as JSON and returns the number of entries written. The file is written to
`<path>.tmp` first and then atomically replaces `path`.

| Parameter | Meaning |
|---|---|
| `cache` | The `TTLCache` to save. Keys must be `str`; values must be JSON serializable. |
| `path` | Destination file (`str` or path like). |
| `include_expired` | Keyword only. Also write entries that expired but were not purged yet, with 0 seconds left. |

Raises `TypeError` if a key is not a string or a value cannot be serialized to JSON.

## `load(path, *, maxsize=None, clock=time.monotonic) -> TTLCache`

Reads a file written by `dump()` and returns a new `TTLCache` with the saved `maxsize` and default `ttl`.

| Parameter | Meaning |
|---|---|
| `path` | File written by `dump()`. |
| `maxsize` | Keyword only. Override the saved maximum size; if the file has more entries, the least recently used ones are dropped. |
| `clock` | Keyword only. Clock for the new cache; remaining time to live is counted from `clock()` at load time. |

Raises `ValueError` if the file's format version is not supported, and the usual `OSError` /
`json.JSONDecodeError` for unreadable files.

## File format

```json
{"version": 1, "maxsize": 128, "ttl": 60, "entries": [["key", "value", 42.5], ["forever", [1, 2], null]]}
```

`entries` is least recently used first; the third item is the seconds left, or `null` for no expiry.

## Example

```python
import os
import tempfile

from shelfcache import TTLCache, dump, load

cache = TTLCache(maxsize=10)                    # no default ttl: entries never expire
cache.set("user:1", {"name": "Ada"}, ttl=300)   # this one expires in 5 minutes
cache.set("config", {"theme": "dark"})

path = os.path.join(tempfile.mkdtemp(), "cache.json")
print(dump(cache, path))         # 2
restored = load(path)
print(restored.get("user:1"))    # {'name': 'Ada'}
print(restored.remaining_ttl("config"))  # None
```
