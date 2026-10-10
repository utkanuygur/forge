"""What build of Forge is running: {version, commit, built_at, channel}.

Resolution order, field by field (the first source that knows a field wins):
1. Environment: FORGE_VERSION, FORGE_COMMIT, FORGE_BUILT_AT, FORGE_CHANNEL (the desktop app sets these).
2. `build-info.json` next to this module. desktop/scripts/build-server.sh writes it into the frozen
   server (`_internal/forge/build-info.json`, beside schema.sql).
3. Dev servers running from a git checkout: `git describe --tags --always --dirty` and
   `git rev-parse --short HEAD`, giving version `<latest tag>+dev` and channel "dev" (cached per process).

Never raises: anything unknown is None. channel is "release", "preview" or "dev"; when a source gives a
version but no channel, the channel follows from the version ("-preview" means preview, "+dev" dev).
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FIELDS = ("version", "commit", "built_at", "channel")
CHANNELS = ("release", "preview", "dev")
ENV = {"version": "FORGE_VERSION", "commit": "FORGE_COMMIT", "built_at": "FORGE_BUILT_AT",
       "channel": "FORGE_CHANNEL"}
BUILD_INFO_FILE = Path(__file__).parent / "build-info.json"
REPO = Path(__file__).resolve().parents[1]


def channel_for(version: str | None) -> str | None:
    if not version:
        return None
    if "-preview" in version:
        return "preview"
    if "+dev" in version:
        return "dev"
    return "release"


def _clean(data: Any) -> dict[str, str | None]:
    """Keeps the known fields as non-empty strings, drops anything else, fills a missing channel."""
    if not isinstance(data, dict):
        return {}
    out = {k: str(data[k]).strip() or None if data.get(k) not in (None, "") else None for k in FIELDS}
    if out["channel"] not in CHANNELS:
        out["channel"] = None
    if out["channel"] is None:
        out["channel"] = channel_for(out["version"])
    return out


def from_env(env: dict[str, str] | None = None) -> dict[str, str | None]:
    env = os.environ if env is None else env
    return _clean({k: env.get(var) for k, var in ENV.items()})


def from_file(path: Path | None = None) -> dict[str, str | None]:
    try:
        return _clean(json.loads((path or BUILD_INFO_FILE).read_text()))
    except (OSError, ValueError):
        return {}


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


@functools.lru_cache(maxsize=4)
def from_git(repo: Path | None = None) -> dict[str, str | None]:
    if getattr(sys, "frozen", False):
        return {}  # a frozen server has no checkout; never pick up an unrelated repository
    repo = repo or REPO
    if not (repo / ".git").exists():
        return {}
    commit = _git(repo, "rev-parse", "--short", "HEAD")
    if not commit:
        return {}
    described = _git(repo, "describe", "--tags", "--always", "--dirty") or ""
    base = described.removesuffix("-dirty")
    m = re.fullmatch(r"(.+)-\d+-g[0-9a-f]+", base)
    tag = m.group(1) if m else base
    # With no tags, `--always` prints the abbreviated commit itself.
    has_tag = bool(tag) and not (re.fullmatch(r"[0-9a-f]+", tag) and (commit.startswith(tag) or tag.startswith(commit)))
    version = f"{tag.removeprefix('v')}+dev" if has_tag else None
    return {"version": version, "commit": commit, "built_at": None, "channel": "dev"}


def build_info(env: dict[str, str] | None = None, file: Path | None = None,
               repo: Path | None = None) -> dict[str, str | None]:
    info: dict[str, str | None] = dict.fromkeys(FIELDS)
    for source in (lambda: from_env(env), lambda: from_file(file), lambda: from_git(repo)):
        if all(info.values()):
            break
        try:
            data = source()
        except Exception:  # noqa: BLE001 - build info must never take the server down
            data = {}
        for k in FIELDS:
            if info[k] is None and data.get(k):
                info[k] = data[k]
    return info
