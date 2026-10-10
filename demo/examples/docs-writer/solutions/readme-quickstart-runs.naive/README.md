# shelfcache

In-process caches for Python.

## Installation

```
pip install shelfcache
```

## Quickstart

```python
from shelfcache import TTLCache, cached

cache = TTLCache(2, 60)
cache.set("a", 1)
print(cache.get("a"))


@cached(256, 30)  # 0.4 style: positional ttl
def square(x):
    return x * x


print(square(4))
```
