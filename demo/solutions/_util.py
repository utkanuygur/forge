"""Helpers for solution scripts. Each script edits the workspace given as argv[1]."""

from __future__ import annotations

import sys
from pathlib import Path

WS = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()


def read(path: str) -> str:
    return (WS / path).read_text()


def write(path: str, text: str) -> None:
    (WS / path).parent.mkdir(parents=True, exist_ok=True)
    (WS / path).write_text(text)


def edit(path: str, old: str, new: str, count: int = 1) -> None:
    text = read(path)
    found = text.count(old)
    if found != count:
        raise SystemExit(f"{path}: expected {count} occurrence(s) of {old!r}, found {found}")
    write(path, text.replace(old, new))


def append(path: str, text: str) -> None:
    write(path, read(path).rstrip("\n") + "\n" + text)


def changelog(entry: str) -> None:
    edit("CHANGELOG.md", "## Unreleased\n\n", f"## Unreleased\n\n- {entry}\n")
