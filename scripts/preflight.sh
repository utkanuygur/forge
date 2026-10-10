#!/usr/bin/env bash
# Pre-demo checklist. Every line prints PASS, WARN or FAIL; exits non-zero if anything FAILs.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

BASE="${FORGE_URL:-http://localhost:8000}"
fails=0
pass() { printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
warn() { printf "  \033[33mWARN\033[0m  %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; fails=$((fails + 1)); }
check() { if eval "$2" >/dev/null 2>&1; then pass "$1"; else "${3:-fail}" "$1"; fi; }

echo "Environment"
check "uv installed"                          "command -v uv"
check "Python package importable"            "uv run python -c 'import forge.server.app'"
check "Docker daemon running"                 "docker info"
check "forge-sandbox image built"             "docker image inspect forge-sandbox"
check "sandbox blocks network"                "! docker run --rm --network none forge-sandbox curl -s -m 3 https://example.com"
check "web app built (web/dist)"              "test -f web/dist/index.html"
check "cloudflared installed"                 "command -v cloudflared" warn

echo "Models"
check "ANTHROPIC_API_KEY set"                 "test -n \"\${ANTHROPIC_API_KEY:-}\""
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  check "Anthropic API reachable with key" \
    "curl -sf -m 10 https://api.anthropic.com/v1/models -H \"x-api-key: \$ANTHROPIC_API_KEY\" -H 'anthropic-version: 2023-06-01'"
fi
check "local model server (Ollama) answering" "curl -sf -m 3 http://localhost:11434/v1/models" warn
if [ -n "${FORGE_VLLM_URL:-}" ]; then
  check "GPU model (vLLM) answering"          "curl -sf -m 20 \"\$FORGE_VLLM_URL/models\"" warn
else
  warn "GPU model: FORGE_VLLM_URL not set"
fi

echo "Server at $BASE"
if curl -sf -m 3 "$BASE/v1/templates" >/dev/null; then
  pass "server answering"
  check "demo agent loaded"                   "curl -sf -m 3 $BASE/v1/agents/repo-maintainer"  warn
  # End-to-end smoke through the real sandbox with a deterministic model.
  slug="preflight-$(date +%s)"
  key=$(curl -sf -X POST "$BASE/v1/agents" -H 'content-type: application/json' \
        -d "{\"slug\":\"$slug\",\"name\":\"Preflight\",\"system_prompt\":\"x\",\"model\":\"fake:hello\"}" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["api_key"])' 2>/dev/null)
  status=$(curl -sf -m 120 -X POST "$BASE/v1/agents/$slug/runs" -H "authorization: Bearer $key" \
        -H 'content-type: application/json' -d '{"input":"smoke","wait":true}' \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' 2>/dev/null)
  [ "$status" = "succeeded" ] && pass "end-to-end run in sandbox (fake model)" || fail "end-to-end run in sandbox (got: ${status:-no response})"
else
  fail "server answering (start it with: make serve)"
fi

echo
if [ "$fails" -eq 0 ]; then echo "Preflight passed."; else echo "Preflight: $fails check(s) failed."; exit 1; fi
