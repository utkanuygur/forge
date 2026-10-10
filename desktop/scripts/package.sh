#!/usr/bin/env bash
# Packages Forge.app (and the dmg with `dmg`) with electron-builder.
# Packaging happens in ~/Library/Caches/forge-desktop because folders synced by iCloud Drive (such as
# Desktop and Documents) get Finder attributes added to app bundles, which breaks code signatures.
# Usage: scripts/package.sh dir|dmg
set -euo pipefail
DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-dmg}"
OUT="${FORGE_DESKTOP_PACKAGE_DIR:-$HOME/Library/Caches/forge-desktop/dist}"
rm -rf "$OUT"
mkdir -p "$OUT" "$DESKTOP/dist"
cd "$DESKTOP"
npx electron-builder --mac "$TARGET" --arm64 -c.directories.output="$OUT"
if [[ "$TARGET" == "dmg" ]]; then
  cp "$OUT"/Forge-*-arm64.dmg "$OUT"/Forge-*-arm64.dmg.blockmap "$DESKTOP/dist/"
  ls -la "$DESKTOP"/dist/Forge-*-arm64.dmg
fi
echo "app bundle: $OUT/mac-arm64/Forge.app"
