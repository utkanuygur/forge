PYTHON ?= python

.PHONY: run test lint check

run:
	PYTHONPATH=src $(PYTHON) -m uvicorn acme.app:app --reload

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	ruff check src tests

check: lint test
