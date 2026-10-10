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

rm -rf "$OUT/server" "$WORK"
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
  --add-data "$REPO/web/dist:web/dist" \
  --collect-submodules forge \
  --collect-submodules uvicorn \
  --collect-submodules sse_starlette \
  --collect-data anthropic \
  --collect-data openai \
  forge/desktop_entry.py

bash "$DESKTOP/scripts/check-min-macos.sh" "$OUT/server/forge-server" "$MIN_MACOS"
echo "built $OUT/server/forge-server"
