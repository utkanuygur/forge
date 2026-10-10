#!/usr/bin/env bash
# Builds the self-contained Forge server (PyInstaller, onedir, arm64) into desktop/build/server/forge-server.
# Also copies sandbox/Dockerfile into desktop/build/sandbox for first-run image builds.
set -euo pipefail

DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$DESKTOP/.." && pwd)"
OUT="$DESKTOP/build"
WORK="$OUT/pyinstaller"

cd "$REPO"

if [[ "${SKIP_WEB_BUILD:-0}" != "1" ]]; then
  if ! (cd web && npm run build); then
    echo "web build failed; using the existing web/dist" >&2
  fi
fi
[[ -f web/dist/index.html ]] || { echo "web/dist/index.html is missing; build the web app first" >&2; exit 1; }

rm -rf "$OUT/server" "$WORK" "$OUT/examples-data" "$OUT/build-info.json"
mkdir -p "$OUT/server" "$OUT/sandbox"
cp sandbox/Dockerfile "$OUT/sandbox/Dockerfile"

# Build with uv's prebuilt (python-build-standalone) CPython in a dedicated environment, never the
# developer's interpreter: a locally compiled Python (pyenv, Homebrew) targets the build machine's
# macOS, so the frozen server would refuse to start on older systems. Anything compiled from source
# during the install targets the same floor as the app (package.json mac.minimumSystemVersion).
MIN_MACOS="$(node -p "require('$DESKTOP/package.json').build.mac.minimumSystemVersion")"
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"
export UV_PROJECT_ENVIRONMENT="${FORGE_BUILD_VENV:-$HOME/Library/Caches/forge-desktop/build-venv}"
export UV_PYTHON_PREFERENCE=only-managed
uv python install 3.12
uv sync --frozen --no-dev --python 3.12

# Example agents (Agents page, "Add example agents"): forge/examples.py reads _internal/demo when frozen.
# Stage only what an install needs (agent.json, evals.json, seed files, demo/repo-maintainer.json,
# demo/evals.json and demo/acme-orders); reference solutions, build scripts and caches stay out.
EXAMPLES_STAGE="$OUT/examples-data/demo"
uv run --no-sync python -m forge.examples stage "$EXAMPLES_STAGE"
[[ -f "$EXAMPLES_STAGE/examples/log-triage/agent.json" ]] || { echo "example staging failed" >&2; exit 1; }
if find "$EXAMPLES_STAGE" -path '*/solutions/*' | grep -q .; then
  echo "staged examples contain solutions/" >&2; exit 1
fi

# Build info (forge/buildinfo.py): the frozen server reads _internal/forge/build-info.json, and the app gets
# the same file as an extraResource. FORGE_BUILD_VERSION (the release version, e.g. 0.1.3 or 0.1.4-preview)
# is optional; without it the version is null and the app's own version fills in at run time.
BUILD_INFO="$OUT/build-info.json"
FORGE_BUILD_VERSION="${FORGE_BUILD_VERSION:-}" \
FORGE_BUILD_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || true)" \
FORGE_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  node -e '
    const v = process.env.FORGE_BUILD_VERSION || null;
    const info = {
      version: v,
      commit: process.env.FORGE_BUILD_COMMIT || null,
      built_at: process.env.FORGE_BUILD_TIME,
      channel: v ? (v.includes("-preview") ? "preview" : "release") : null,
    };
    require("fs").writeFileSync(process.argv[1], JSON.stringify(info, null, 2) + "\n");
  ' "$BUILD_INFO"
echo "build info: $(tr -d '\n ' < "$BUILD_INFO")"

# forge/db.py reads schema.sql next to its own __file__, and app.py serves web/dist from
# parents[2] of forge/server/app.py. Frozen, both resolve inside _internal, so place the data there.
uv run --no-sync --with pyinstaller pyinstaller \
  --noconfirm --clean --onedir \
  --name forge-server \
  --target-arch arm64 \
  --paths "$REPO" \
  --distpath "$OUT/server" \
  --workpath "$WORK" \
  --specpath "$WORK" \
  --add-data "$REPO/forge/schema.sql:forge" \
  --add-data "$BUILD_INFO:forge" \
  --add-data "$REPO/web/dist:web/dist" \
  --add-data "$EXAMPLES_STAGE:demo" \
  --collect-submodules forge \
  --collect-submodules uvicorn \
  --collect-submodules sse_starlette \
  --collect-data anthropic \
  --collect-data openai \
  forge/desktop_entry.py

bash "$DESKTOP/scripts/check-min-macos.sh" "$OUT/server/forge-server" "$MIN_MACOS"
echo "built $OUT/server/forge-server"
