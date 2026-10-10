"""The `cached` decorator: memoize a function's results in a TTLCache."""

from __future__ import annotations

import functools
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from shelfcache.cache import TTLCache

F = TypeVar("F", bound=Callable[..., Any])


def _default_key(*args: Any, **kwargs: Any) -> Hashable:
    return (args, tuple(sorted(kwargs.items())))


def cached(maxsize: int = 128, *, ttl: float | None = None,
           key: Callable[..., Hashable] | None = None) -> Callable[[F], F]:
    """Memoize a function with a TTLCache.

    Args:
        maxsize: Maximum number of cached results.
        ttl: Seconds each result stays valid, or None to keep results until evicted. Keyword only.
        key: Function computing the cache key from the call's arguments. The default uses all
            positional and keyword arguments, which must then be hashable.

    Returns:
        A decorator. The decorated function has a `cache` attribute (the TTLCache) and a
        `cache_clear()` method.

    Example:
        >>> @cached(maxsize=2, ttl=60)
        ... def square(x):
        ...     return x * x
        >>> square(3)
        9
        >>> square.cache.stats().misses
        1
    """
    make_key = key or _default_key

    def decorate(fn: F) -> F:
        cache = TTLCache(maxsize=maxsize, ttl=ttl)
        sentinel = object()

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            k = make_key(*args, **kwargs)
            value = cache.get(k, sentinel)
            if value is sentinel:
                value = fn(*args, **kwargs)
                cache.set(k, value)
            return value

        wrapper.cache = cache  # type: ignore[attr-defined]
        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate
