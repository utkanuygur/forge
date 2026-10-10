"""shelfcache: small in-process caches with least recently used eviction and per entry expiry."""

from shelfcache.cache import CacheStats, TTLCache
from shelfcache.decorators import cached
from shelfcache.serialize import dump, load

__all__ = ["CacheStats", "TTLCache", "cached", "dump", "load"]
__version__ = "0.5.0.dev0"
