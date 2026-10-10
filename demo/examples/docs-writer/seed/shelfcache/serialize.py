"""Save a TTLCache to a JSON file and load it back, keeping each entry's remaining time to live."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

from shelfcache.cache import TTLCache

FORMAT_VERSION = 1


def dump(cache: TTLCache, path: str | os.PathLike[str], *, include_expired: bool = False) -> int:
    """Write `cache` to `path` as JSON and return the number of entries written.

    Keys must be strings and values JSON serializable. Entries are written least recently used first,
    so loading the file restores the same eviction order. The file is replaced atomically.

    Args:
        cache: The cache to save.
        path: Destination file.
        include_expired: Also write entries that have expired but were not purged yet (with 0
            seconds left, so they expire immediately after loading).

    Raises:
        TypeError: If a key is not a string or a value is not JSON serializable.
    """
    now = cache._clock()
    entries = []
    for key, (value, expires_at) in cache._data.items():
        if not isinstance(key, str):
            raise TypeError(f"only string keys can be saved, got {type(key).__name__}")
        remaining = None if expires_at is None else expires_at - now
        if remaining is not None and remaining <= 0:
            if not include_expired:
                continue
            remaining = 0.0
        entries.append([key, value, remaining])
    doc = {"version": FORMAT_VERSION, "maxsize": cache.maxsize, "ttl": cache.ttl, "entries": entries}
    tmp = f"{os.fspath(path)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)
    return len(entries)


def load(path: str | os.PathLike[str], *, maxsize: int | None = None,
         clock: Callable[[], float] = time.monotonic) -> TTLCache:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("version") != FORMAT_VERSION:
        raise ValueError(f"unsupported cache file version {doc.get('version')!r}")
    cache = TTLCache(maxsize=maxsize or doc["maxsize"], ttl=doc["ttl"], clock=clock)
    for key, value, remaining in doc["entries"]:
        if remaining is None:
            cache._data[key] = (value, None)
        else:
            cache._data[key] = (value, clock() + remaining)
        if len(cache._data) > cache.maxsize:
            cache._data.popitem(last=False)
    return cache
