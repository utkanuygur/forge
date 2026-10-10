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

# forge/db.py reads schema.sql next to its own __file__, and app.py serves web/dist from
# parents[2] of forge/server/app.py. Frozen, both resolve inside _internal, so place the data there.
uv run --with pyinstaller pyinstaller \
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

echo "built $OUT/server/forge-server"
