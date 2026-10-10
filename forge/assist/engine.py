"""One assist call: structured output from the grader, validation, one retry listing the problems."""

from __future__ import annotations

import json
from typing import Any

from forge.assist.registry import AssistKind, Prepared, envelope_schema
from forge.types import Message, ModelResponse, Provider, ToolSpec


class AssistError(Exception):
    """The model did not produce a valid suggestion after one retry. `errors` lists what was wrong."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


class StructuredGrader:
    """Provider adapter that asks Claude for JSON matching `schema` via structured outputs
    (`output_config.format`); other providers get the plain prompt and are parsed leniently. No temperature is
    sent (current Claude models reject sampling parameters). Wrap it in MeteredProvider to record usage."""

    def __init__(self, inner: Provider, schema: dict[str, Any]):
        self.inner, self.schema = inner, schema

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       temperature: float | None = None) -> ModelResponse:
        from forge.providers import MAX_TOKENS, AnthropicProvider, with_retry

        if isinstance(self.inner, AnthropicProvider):
            kwargs: dict[str, Any] = {
                "model": self.inner.model, "max_tokens": MAX_TOKENS, "system": system,
                "messages": [{"role": "user", "content": m.text} for m in messages],
                "output_config": {"format": {"type": "json_schema", "schema": self.schema}},
            }
            resp = await with_retry(lambda: self.inner.client.messages.create(**kwargs))
            usage = getattr(resp, "usage", None)
            return ModelResponse(
                text="\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text"),
                tokens_in=getattr(usage, "input_tokens", 0) or 0, tokens_out=getattr(usage, "output_tokens", 0) or 0,
                stop_reason=getattr(resp, "stop_reason", None))
        return await self.inner.complete(system, messages, tools, temperature)


def structured(spec: AssistKind, grader: Provider) -> StructuredGrader:
    return StructuredGrader(grader, envelope_schema(spec))


def parse_reply(text: str) -> tuple[dict[str, Any] | None, str]:
    """(result, rationale) from the model's JSON; result is None when the shape is wrong. Strings are trimmed."""
    from forge.improve import _first_json_object

    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        obj = _first_json_object(text or "")
    if not isinstance(obj, dict) or not isinstance(obj.get("result"), dict):
        return None, ""
    result = {k: v.strip() if isinstance(v, str) else v for k, v in obj["result"].items()}
    rationale = obj.get("rationale")
    return result, rationale.strip() if isinstance(rationale, str) else ""


def check(spec: AssistKind, result: dict[str, Any] | None, data: dict[str, Any]) -> list[str]:
    if result is None:
        return ['the reply was not a JSON object {"result": {...}, "rationale": "..."}']
    missing = [k for k in spec.schema.get("required", []) if k not in result]
    if missing:
        return [f"result is missing {', '.join(missing)}"]
    return spec.validate(result, data)


async def _ask(spec: AssistKind, grader: Provider, user: str) -> str:
    resp = await grader.complete(spec.system_prompt, [Message(role="user", text=user)], [], None)
    if getattr(resp, "stop_reason", None) == "refusal":
        raise AssistError("The model declined this request.")
    return resp.text or ""


def redact(value: Any, secrets: list[str]) -> Any:
    """Replaces any secret value that appears in the result (defense in depth: values never enter the context)."""
    if isinstance(value, str):
        for s in secrets:
            if s and len(s) >= 4:
                value = value.replace(s, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    return value


async def run_assist(spec: AssistKind, prepared: Prepared, grader: Provider) -> dict[str, Any]:
    """Returns {"result": {...}, "rationale": str}. `grader` should already be structured and metered."""
    text = await _ask(spec, grader, prepared.text)
    result, rationale = parse_reply(text)
    errs = check(spec, result, prepared.data)
    if errs:
        retry = (f"{prepared.text}\n\n<previous_attempt>\n{text[:4000]}\n</previous_attempt>\n\n<problems>\n"
                 + "\n".join(f"- {e}" for e in errs)
                 + "\n</problems>\n\nThe previous attempt was invalid. Return a corrected JSON object that fixes "
                   "every problem.")
        text = await _ask(spec, grader, retry)
        result, rationale = parse_reply(text)
        errs = check(spec, result, prepared.data)
        if errs:
            raise AssistError(f"The model could not write a valid {spec.kind} suggestion: " + "; ".join(errs[:4]),
                              errs)
    return {"result": result, "rationale": rationale}
