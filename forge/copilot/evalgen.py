"""Adapter between the assistant's generate_eval_cases tool and the assist registry.

Uses feat/assist's canonical batch kind `eval_cases` (forge.assist.kinds.eval_case) through POST /v1/assist/eval_cases,
so calls are metered like any assist call, and returns plain case dicts {name, input, checker_type, checker, split}
ready for POST /agents/{slug}/evals.
"""

from __future__ import annotations

from typing import Any

from forge.assist import registry
from forge.copilot.api_client import ForgeApi, ToolError

KIND = "eval_cases"
MAX_COUNT = 5
CASE_KEYS = ("name", "input", "checker_type", "checker", "split")


def parse_count(raw: Any) -> int:
    try:
        n = int(raw) if raw is not None else 3
    except (TypeError, ValueError):
        raise ValueError("count must be a number from 1 to 5") from None
    if not 1 <= n <= MAX_COUNT:
        raise ValueError("count must be a number from 1 to 5")
    return n


def active_kind() -> str:
    if registry.get(KIND) is None:  # pragma: no cover  (forge.assist registers it on import)
        raise ToolError("no eval case generator is registered")
    return KIND


def _case(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not all(k in raw for k in ("name", "input", "checker_type", "checker")):
        raise ToolError("the generator returned an incomplete eval case")
    out = {k: raw[k] for k in CASE_KEYS if k in raw}
    out.setdefault("split", "train")
    return out


async def generate(api: ForgeApi, slug: str, focus: str, count: int, split: str | None) -> list[dict[str, Any]]:
    kind = active_kind()
    inp: dict[str, Any] = {"focus": focus, "n": count}
    if split == "holdout":
        inp["holdout"] = count
    elif split == "train":
        inp["holdout"] = 0
    reply = await api.post(f"/assist/{kind}", {"slug": slug, "input": inp})
    result = reply["result"]
    raw = result.get("cases") if isinstance(result, dict) and "cases" in result else [result]
    return [_case(c) for c in raw][:count]
