#!/usr/bin/env bash
# Fails if any Mach-O file under DIR requires a newer macOS than MIN (for example 12.0), or links a
# library outside the system and the bundle (Homebrew, pyenv). Usage: check-min-macos.sh DIR MIN
set -euo pipefail
DIR="$1"; MIN="$2"
bad=0; checked=0

newer() { [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" == "$1" && "$1" != "$2" ]]; }

while IFS= read -r -d '' f; do
  file -b "$f" | grep -q "Mach-O" || continue
  checked=$((checked + 1))
  minos="$(otool -l "$f" | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit} /LC_VERSION_MIN_MACOSX/{v=1} v&&/version/{print $2; exit}')"
  if [[ -n "$minos" ]] && newer "$minos" "$MIN"; then
    echo "requires macOS $minos (> $MIN): ${f#$DIR/}" >&2; bad=1
  fi
  while read -r lib; do
    case "$lib" in /usr/lib/*|/System/*|@rpath/*|@loader_path/*|@executable_path/*) ;;
      *) echo "links outside the bundle: ${f#$DIR/} -> $lib" >&2; bad=1 ;; esac
  done < <(otool -L "$f" | tail -n +2 | awk '{print $1}')
done < <(find "$DIR" -type f -print0)

if [[ "$bad" -ne 0 ]]; then
  echo "macOS compatibility check failed for $DIR (minimum $MIN)" >&2; exit 1
fi
echo "macOS compatibility check passed: $checked binaries, minimum $MIN"
