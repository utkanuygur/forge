"""Adapter between the assistant's generate_eval_cases tool and the assist registry.

Preference order: the batch kind `eval_cases`, then the single kind `eval_case` called `count` times (both from
feat/assist), else this branch's private fallback `copilot_eval_cases` (forge.copilot.eval_fallback; replace with
feat/assist kind at merge). Every path goes through POST /v1/assist/<kind>, so calls are metered like any assist
call, and returns plain case dicts {name, input, checker_type, checker, split} ready for POST /agents/{slug}/evals.
"""

from __future__ import annotations

from typing import Any

from forge.assist import registry
from forge.copilot import eval_fallback
from forge.copilot.api_client import ForgeApi, ToolError

CASE_KEYS = ("name", "input", "checker_type", "checker", "split")


def active_kind() -> str:
    if registry.get("eval_cases") is not None:
        return "eval_cases"
    if registry.get("eval_case") is not None:
        return "eval_case"
    kind = eval_fallback.ensure_registered()
    if kind is None:  # pragma: no cover  (only if the registry changes between the checks above)
        raise ToolError("no eval case generator is registered")
    return kind


def _case(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not all(k in raw for k in ("name", "input", "checker_type", "checker")):
        raise ToolError("the generator returned an incomplete eval case")
    out = {k: raw[k] for k in CASE_KEYS if k in raw}
    out.setdefault("split", "train")
    return out


async def generate(api: ForgeApi, slug: str, focus: str, count: int, split: str | None) -> list[dict[str, Any]]:
    kind = active_kind()
    base: dict[str, Any] = {"focus": focus} | ({"split": split} if split else {})
    if kind == "eval_case":
        cases: list[dict[str, Any]] = []
        for _ in range(count):
            reply = await api.post("/assist/eval_case", {"slug": slug, "input": base,
                                                         "avoid": [c["input"] for c in cases]})
            cases.append(_case(reply["result"]))
        return cases
    reply = await api.post(f"/assist/{kind}", {"slug": slug, "input": base | {"count": count}})
    result = reply["result"]
    raw = result.get("cases") if isinstance(result, dict) and "cases" in result else [result]
    return [_case(c) for c in raw][:count]


eval_fallback.ensure_registered()  # registers the private kind only when feat/assist's kinds are absent
