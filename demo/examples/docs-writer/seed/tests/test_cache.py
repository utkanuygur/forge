import pytest
from shelfcache import CacheStats, TTLCache, cached, dump, load


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_lru_eviction_and_stats():
    c = TTLCache(maxsize=2)
    c["a"] = 1
    c["b"] = 2
    assert c.get("a") == 1  # a is now most recently used
    c["c"] = 3
    assert "b" not in c and "a" in c and len(c) == 2
    assert c.get("zzz", "dflt") == "dflt"
    assert c.stats() == CacheStats(hits=1, misses=1, evictions=1, expirations=0)


def test_expiry_counts_as_miss():
    clock = Clock()
    c = TTLCache(ttl=10, clock=clock)
    c.set("k", "v")
    c.set("short", "v", ttl=1)
    assert c.remaining_ttl("k") == 10
    clock.t += 5
    assert c.get("short") is None and c.get("k") == "v"
    clock.t += 6
    assert c.purge() == 1
    assert c.stats().hits == 1 and c.stats().misses == 1 and c.stats().expirations == 2


def test_validation():
    with pytest.raises(ValueError):
        TTLCache(maxsize=0)
    with pytest.raises(ValueError):
        TTLCache(ttl=0)
    with pytest.raises(ValueError):
        TTLCache().set("k", 1, ttl=-1)
    with pytest.raises(KeyError):
        TTLCache()["missing"]


def test_pop_delete_clear_and_deprecated_expire():
    c = TTLCache()
    c["a"] = 1
    assert c.pop("a") == 1 and c.pop("a", None) is None
    with pytest.raises(KeyError):
        c.pop("a")
    c["b"] = 2
    assert c.delete("b") is True and c.delete("b") is False
    c["x"] = 1
    c.get("x")
    c.clear()
    assert len(c) == 0 and c.stats() == CacheStats(0, 0, 0, 0)
    with pytest.warns(DeprecationWarning):
        assert c.expire() == 0


def test_cached_decorator():
    calls = []

    @cached(maxsize=4, ttl=60)
    def double(x):
        calls.append(x)
        return 2 * x

    assert double(2) == 4 and double(2) == 4 and calls == [2]
    assert double.cache.stats().hits == 1
    double.cache_clear()
    assert double(2) == 4 and calls == [2, 2]

    @cached(key=lambda items: tuple(items))
    def total(items):
        return sum(items)

    assert total([1, 2]) == 3 and total([1, 2]) == 3 and total.cache.stats().hits == 1


def test_dump_and_load_keep_remaining_ttl(tmp_path):
    clock = Clock()
    c = TTLCache(maxsize=3, ttl=30, clock=clock)
    c.set("a", {"n": 1})
    c.set("b", [1, 2], ttl=60)
    clock.t += 10
    path = tmp_path / "cache.json"
    assert dump(c, path) == 2
    loaded = load(path, clock=clock)
    assert loaded.get("a") == {"n": 1} and loaded.remaining_ttl("a") == 20
    assert loaded.maxsize == 3
    with pytest.raises(TypeError):
        bad = TTLCache()
        bad[1] = "x"
        dump(bad, tmp_path / "bad.json")
