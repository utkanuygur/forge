"""Lesson provenance: per-lesson metadata stored in a version's `lesson_meta` JSON field (see CONTRACT.md).

Kept separate from forge.improve so the server can record provenance without depending on optimizer internals.
"""

from __future__ import annotations

import time
from typing import Any


def lesson_provenance(old_lessons: list[str], old_meta: dict | None, new_lessons: list[str],
                      edits: list[Any], version_n: int, *, origin: str = "optimizer",
                      at: float | None = None) -> dict[str, dict]:
    """Per-lesson provenance for `new_lessons`, keyed by lesson text (lessons are unique within a version).

    A lesson carried over unchanged keeps its existing entry (none if it never had one). A lesson that is new in
    this version gets {created_in_version, evidence_run_ids, origin, op, created_at} from the edit that made it,
    plus `edited_from` when it replaced an older lesson.
    """
    old_meta = old_meta or {}
    made = {e.text: e for e in edits if getattr(e, "op", None) in ("add", "edit")}
    out: dict[str, dict] = {}
    for text in new_lessons:
        if text in old_lessons:
            if text in old_meta:
                out[text] = old_meta[text]
            continue
        e = made.get(text)
        entry: dict[str, Any] = {"created_in_version": version_n,
                                 "evidence_run_ids": list(getattr(e, "evidence_run_ids", None) or []),
                                 "origin": origin, "op": e.op if e else "add",
                                 "created_at": time.time() if at is None else at}
        if e is not None and e.op == "edit" and getattr(e, "old_text", None):
            entry["edited_from"] = e.old_text
        out[text] = entry
    return out
