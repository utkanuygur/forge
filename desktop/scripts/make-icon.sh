#!/usr/bin/env bash
# Generates desktop/assets/icon.png (1024px) and desktop/assets/icon.icns with sips and iconutil.
set -euo pipefail
DESKTOP="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$DESKTOP/assets"
mkdir -p "$ASSETS"
uv run --no-project --with pillow python "$DESKTOP/scripts/make-icon.py" "$ASSETS/icon.png"
SET="$(mktemp -d)/icon.iconset"
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ASSETS/icon.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$ASSETS/icon.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$ASSETS/icon.icns"
echo "wrote $ASSETS/icon.png and $ASSETS/icon.icns"
