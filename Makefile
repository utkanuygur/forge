# Forge developer workflow. `make help` lists targets.
SHELL := /bin/bash
PY    := uv run
PTH   := .venv/lib/python3.12/site-packages

.PHONY: help setup image fix-pth test test-unit test-docker test-e2e test-web test-web-unit test-web-e2e \
        test-demo test-live test-all lint preflight rehearse serve web dev clean examples test-examples

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies, build the sandbox image
	uv sync
	@$(MAKE) -s fix-pth
	cd web && npm install
	@$(MAKE) -s image

fix-pth: ## macOS: unhide the editable-install .pth file (uv sync can mark it hidden)
	@chflags nohidden $(PTH)/*.pth 2>/dev/null || true

image: ## Build the forge-sandbox Docker image
	docker build -q -t forge-sandbox sandbox/

# ---------------------------------------------------------------- test layers (fast to slow)

test-unit: fix-pth ## Layer 1: unit tests, no Docker, no network (seconds)
	$(PY) pytest -q -m "not docker and not e2e and not live"

test-docker: fix-pth ## Layer 2: real sandbox containers (isolation, limits, export)
	$(PY) pytest -q -m docker

test-e2e: fix-pth ## Layer 3: real server + Docker + deterministic fake models, incl. full improvement loop
	$(PY) pytest -q -m e2e

test-web-unit: ## Layer 4a: web component and hook tests (Vitest)
	cd web && npm test

test-web-e2e: fix-pth ## Layer 4b: browser flows against the real backend (Playwright)
	cd web && npm run test:e2e

test-web: test-web-unit test-web-e2e ## Layer 4: all web tests

test-demo: ## Layer 5: demo eval checkers fail on seed and naive fixes, pass on reference fixes
	$(PY) python demo/verify.py

test-live: fix-pth ## Layer 6: real model APIs (needs ANTHROPIC_API_KEY; optional local/GPU endpoints)
	FORGE_LIVE=1 $(PY) pytest -q -m live

test: fix-pth ## Default gate: layers 1 to 3 (everything that needs no API key or browser)
	$(PY) pytest -q -m "not live"

test-all: test test-web test-demo ## Every deterministic layer; run before each commit to main

lint: ## Static checks
	$(PY) python -m compileall -q forge
	cd web && npx tsc -b

# ---------------------------------------------------------------- run and rehearse

preflight: fix-pth ## Pre-demo checklist: environment, services, keys, end-to-end smoke
	@bash scripts/preflight.sh

rehearse: fix-pth ## Real improvement loop on the demo agent with Claude (needs server + key)
	$(PY) python scripts/rehearse.py

web: ## Build the web app into web/dist (served by the API server at /)
	cd web && npm run build

serve: fix-pth web ## Run the server on :8000 with the built web app
	$(PY) uvicorn forge.server.app:app --port 8000

dev: fix-pth ## API with reload on :8000; run `cd web && npm run dev` for the UI on :5173
	$(PY) uvicorn forge.server.app:app --port 8000 --reload

clean: ## Remove local data and leftover sandbox containers
	rm -rf data
	-docker ps -aq --filter ancestor=forge-sandbox | xargs -r docker rm -f

# ---------------------------------------------------------------- example agents

.PHONY: examples test-examples

examples: fix-pth ## Load the example agents, suggested prompts and eval cases (needs a running server; BASE_URL=...)
	$(PY) python demo/load_examples.py --base-url $(or $(BASE_URL),http://localhost:8000)

test-examples: ## Example eval and suggested prompt checkers fail on the seed and pass on reference solutions
	$(PY) python demo/verify_examples.py
