"""docs_lookup: keyword search over Forge's own documentation (README, CONTRACT, TESTING and docs/*.md).

Files are split into sections at Markdown headings; a section scores by how many query terms appear in its heading
(weighted) and body. The index is cached per file modification time. The frozen desktop build may not ship the
docs, in which case the lookup says so instead of failing.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

STOP = {"the", "and", "for", "how", "can", "does", "what", "with", "this", "that", "are", "you", "use", "into",
        "from", "when", "why", "who", "which", "about", "should", "would", "could", "there", "their", "your",
        "forge", "please", "tell", "explain", "show", "work", "works", "get", "set"}
MAX_SECTION_CHARS = 1500
HEADING = re.compile(r"^(#{1,4})\s+(.*)$")


@dataclass
class Section:
    path: str
    heading: str
    body: str


def docs_root() -> Path:
    if env := os.environ.get("FORGE_DOCS_DIR"):
        return Path(env)
    if (meipass := getattr(sys, "_MEIPASS", None)) is not None:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


def doc_files(root: Path | None = None) -> list[Path]:
    root = root or docs_root()
    files = [root / n for n in ("README.md", "CONTRACT.md", "TESTING.md")]
    files += sorted((root / "docs").glob("*.md")) if (root / "docs").is_dir() else []
    return [f for f in files if f.is_file()]


_cache: dict[str, tuple[float, list[Section]]] = {}


def sections(path: Path, root: Path) -> list[Section]:
    key, mtime = str(path), path.stat().st_mtime
    if (hit := _cache.get(key)) and hit[0] == mtime:
        return hit[1]
    rel = path.relative_to(root).as_posix()
    out: list[Section] = []
    heading, lines = rel, []
    for line in path.read_text(errors="replace").splitlines():
        if m := HEADING.match(line):
            if "".join(lines).strip():
                out.append(Section(rel, heading, "\n".join(lines).strip()))
            heading, lines = m.group(2).strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        out.append(Section(rel, heading, "\n".join(lines).strip()))
    _cache[key] = (mtime, out)
    return out


def terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", query.lower())
    return list(dict.fromkeys(w for w in words if len(w) >= 3 and w not in STOP))


def search(query: str, limit: int = 3, root: Path | None = None) -> list[tuple[Section, float]]:
    root = root or docs_root()
    want = terms(query)
    if not want:
        return []
    scored = []
    for f in doc_files(root):
        for s in sections(f, root):
            head, body = s.heading.lower(), s.body.lower()
            hits = [t for t in want if t in head or t in body]
            if not hits:
                continue
            score = sum(3.0 for t in want if t in head) + sum(min(body.count(t), 5) for t in want)
            score *= len(hits) / len(want)  # sections covering more of the query rank first
            if "/" not in s.path:
                score *= 1.25  # the maintained top-level docs before notes under docs/
            scored.append((s, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def lookup(query: str, limit: int = 3) -> str:
    root = docs_root()
    if not doc_files(root):
        return "The documentation files are not available in this build."
    found = search(query, limit, root)
    if not found:
        return f"No documentation section matches {query!r}. Try other words."
    parts = []
    for s, _ in found:
        body = s.body if len(s.body) <= MAX_SECTION_CHARS else s.body[:MAX_SECTION_CHARS].rsplit("\n", 1)[0] + "\n[...]"
        parts.append(f"## {s.path} > {s.heading}\n{body}")
    return "\n\n".join(parts)
