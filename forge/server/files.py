"""Read-only browsing of a run's exported workspace archive (`runs/<id>/workspace.tar.gz`).

Nothing is ever extracted to disk. Paths are normalized and checked before lookup, `.git` is hidden, and
links are only followed when they resolve to a regular file inside the same archive.
"""

from __future__ import annotations

import codecs
import functools
import os
import posixpath
import re
import tarfile
from dataclasses import dataclass

MAX_TEXT_BYTES = 1_000_000  # largest file served as text
MAX_ENTRIES = 10_000  # listing cap
SNIFF_BYTES = 8192
MAX_LINK_HOPS = 8


class FileError(Exception):
    """Raised with an HTTP status: 400 bad path, 404 missing, 413 too large, 415 binary."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    is_text: bool


def normalize(name: str) -> str | None:
    """Archive-relative POSIX path, or None when it is empty, absolute after cleanup, escapes or is in .git."""
    name = name.replace("\\", "/")
    if name.startswith("/") or "\0" in name:
        return None
    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    if not parts or ".git" in parts:
        return None
    return "/".join(parts)


def _member_name(m: tarfile.TarInfo) -> str | None:
    # Archives are written with `tar -C root .`, so members look like "./hello.py". Anything that would
    # land outside the root (absolute or with "..") is dropped rather than reinterpreted.
    return normalize(m.name)


def _link_target(member_path: str, m: tarfile.TarInfo) -> str | None:
    """Archive path a symlink or hard link points to, or None when it leaves the archive."""
    target = m.linkname.replace("\\", "/")
    if target.startswith("/"):
        return None
    if m.issym():
        joined = posixpath.join(posixpath.dirname(member_path), target)
    else:  # hard links name another member relative to the archive root
        joined = target
    # Resolve ".." textually; a path that climbs above the root is outside the archive.
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return normalize("/".join(parts)) if parts else None


def _is_text(head: bytes) -> bool:
    if b"\0" in head:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


class Archive:
    """Index of one workspace tarball. Build with `open_archive` (cached by path, mtime and size)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.members: dict[str, tarfile.TarInfo] = {}
        with tarfile.open(path, mode="r:gz") as tar:
            for m in tar:
                if len(self.members) >= MAX_ENTRIES * 2:
                    break
                name = _member_name(m)
                if name is None or not (m.isfile() or m.issym() or m.islnk()):
                    continue
                self.members[name] = m
        self._entries: list[Entry] | None = None

    def resolve(self, path: str) -> tarfile.TarInfo | None:
        """The regular-file member a path refers to, following in-archive links; None if missing or unsafe."""
        cur = path
        for _ in range(MAX_LINK_HOPS + 1):
            m = self.members.get(cur)
            if m is None:
                return None
            if m.isfile():
                return m
            nxt = _link_target(cur, m)
            if nxt is None:
                return None
            cur = nxt
        return None

    def entries(self) -> list[Entry]:
        if self._entries is not None:
            return self._entries
        out: list[Entry] = []
        with tarfile.open(self.path, mode="r:gz") as tar:
            heads: dict[str, bytes] = {}
            for m in tar:
                if m.isfile() and _member_name(m) in self.members:
                    f = tar.extractfile(m)
                    heads[m.name] = f.read(SNIFF_BYTES) if f else b""
        for name in sorted(self.members):
            target = self.resolve(name)
            if target is None:
                continue
            out.append(Entry(name, target.size, _is_text(heads.get(target.name, b""))))
            if len(out) >= MAX_ENTRIES:
                break
        self._entries = out
        return out

    def read(self, path: str) -> bytes:
        """Bytes of one text file. Raises FileError (400, 404, 413 or 415)."""
        norm = normalize(path)
        if norm is None:
            raise FileError(400, "invalid path")
        member = self.resolve(norm)
        if member is None:
            raise FileError(404, f"{norm} not found in the workspace")
        with tarfile.open(self.path, mode="r:gz") as tar:
            f = tar.extractfile(member.name)
            if f is None:
                raise FileError(404, f"{norm} not found in the workspace")
            head = f.read(SNIFF_BYTES)
            if not _is_text(head):
                raise FileError(415, f"{norm} is a binary file")
            if member.size > MAX_TEXT_BYTES:
                raise FileError(413, f"{norm} is larger than 1 MB")
            return head + f.read(MAX_TEXT_BYTES)


@functools.lru_cache(maxsize=32)
def _cached(path: str, mtime_ns: int, size: int) -> Archive:  # noqa: ARG001 (cache key)
    return Archive(path)


def open_archive(path: str) -> Archive:
    st = os.stat(path)
    try:
        return _cached(path, st.st_mtime_ns, st.st_size)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise FileError(404, f"workspace archive is unreadable: {exc}") from exc


# ---------------------------------------------------------------- changed files from the stored diff

_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def _unquote(s: str) -> str:
    """Git C-style quoted path ("a/x\\ty" with octal escapes) to text."""
    raw = s.encode("latin-1", "backslashreplace")
    out = bytearray()
    i = 0
    simple = {b"n": b"\n", b"t": b"\t", b'"': b'"', b"\\": b"\\", b"a": b"\a", b"b": b"\b", b"f": b"\f",
              b"r": b"\r", b"v": b"\v"}
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in simple:
                out += simple[nxt]
                i += 2
                continue
            if raw[i + 1:i + 4].isdigit():
                out.append(int(raw[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out += c
        i += 1
    return out.decode("utf-8", errors="replace")


def _diff_path(token: str, prefix: str) -> str | None:
    token = token.strip()
    if token == "/dev/null":
        return None
    m = _QUOTED.match(token)
    if m:
        token = _unquote(m.group(1))
    token = token.removeprefix(prefix)
    return normalize(token)


def changed_files(diff: str) -> dict[str, str]:
    """Maps each path in a unified git diff to "added", "modified", "deleted" or "renamed"."""
    out: dict[str, str] = {}
    cur_a = cur_b = None
    kind = "modified"

    def commit() -> None:
        if kind == "deleted":
            if cur_a:
                out[cur_a] = "deleted"
        elif cur_b:
            out[cur_b] = kind

    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if in_header or cur_a or cur_b:
                commit()
            cur_a = cur_b = None
            kind, in_header = "modified", True
            rest = line[len("diff --git "):]
            if rest.startswith('"'):
                m = _QUOTED.match(rest)
                if m:
                    cur_a = _diff_path(rest[:m.end()], "a/")
                    cur_b = _diff_path(rest[m.end():], "b/")
            else:
                # Unquoted "a/X b/X": split in the middle when both sides are equal (paths with spaces).
                body = rest
                half = (len(body) - 1) // 2
                if body[half:half + 1] == " " and body[:half].removeprefix("a/") == body[half + 1:].removeprefix("b/"):
                    cur_a = _diff_path(body[:half], "a/")
                    cur_b = _diff_path(body[half + 1:], "b/")
                elif " b/" in body:
                    a, _, b = body.partition(" b/")
                    cur_a, cur_b = _diff_path(a, "a/"), _diff_path("b/" + b, "b/")
            continue
        if not in_header:
            continue
        if line.startswith("@@") or line.startswith("GIT binary patch"):
            in_header = False
        elif line.startswith("new file mode"):
            kind = "added"
        elif line.startswith("deleted file mode"):
            kind = "deleted"
        elif line.startswith("rename to "):
            kind, cur_b = "renamed", _diff_path(line[len("rename to "):], "")
        elif line.startswith("rename from "):
            cur_a = _diff_path(line[len("rename from "):], "")
        elif line.startswith("--- "):
            if (p := _diff_path(line[4:], "a/")) is not None:
                cur_a = p
        elif line.startswith("+++ "):
            if (p := _diff_path(line[4:], "b/")) is not None:
                cur_b = p
    if cur_a or cur_b:
        commit()
    return out
