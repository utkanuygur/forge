# Contributing to shelfcache

## Set up

```
git clone <repo> && cd shelfcache
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
```

shelfcache has no runtime dependencies and supports Python 3.10 or newer.

## Run the tests

```
python -m pytest -q
```

Add or update a test in `tests/` for every behavior change. Use a fake `clock` (see `tests/test_cache.py`)
instead of sleeping.

## Docstrings

Public functions, classes and methods use Google style docstrings: a one line summary, then `Args:`,
`Returns:` and `Raises:` sections where relevant.

## Record your change

Add one line under `## Unreleased` in `CHANGELOG.md`, in the right group (`Added`, `Changed`,
`Deprecated`, `Fixed`), ending with the pull request number, for example
`- TTLCache.purge() returns how many entries it removed. (#39)`. Mark breaking changes with
**Breaking:** and show the migration.
