"""Memoize a slow lookup with @cached and a ttl, then show the hit and the cache stats."""

import time

from shelfcache import cached


@cached(maxsize=64, ttl=30)
def exchange_rate(currency: str) -> float:
    time.sleep(0.5)  # stands in for a slow API call
    return {"EUR": 0.92, "GBP": 0.79, "JPY": 151.3}[currency]


for attempt in (1, 2):
    start = time.perf_counter()
    rate = exchange_rate("EUR")
    print(f"call {attempt}: EUR rate {rate} in {time.perf_counter() - start:.3f}s")

print("stats:", exchange_rate.cache.stats())
