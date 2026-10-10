"""Model prices for cost tracking (USD per million tokens, input and output).

Anthropic models are metered at list price. OpenAI-compatible endpoints (local or GPU) and `fake:` test
models cost nothing here: their GPU time is billed by the GPU provider, so they are reported as
"self-hosted, not metered". Unknown Anthropic models have no price (cost is null).

Override or extend with `FORGE_PRICES`, a JSON object whose keys are either a full model string
(`"fake:hello"`, `"openai_compat:http://host/v1|qwen3:4b-instruct"`) or a bare Anthropic id (`"claude-x"`), and whose
values are `[input, output]` or `{"input": .., "output": ..}`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

log = logging.getLogger("forge.pricing")

# USD per million tokens: (input, output)
ANTHROPIC_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

PriceKind = Literal["metered", "self_hosted", "unknown"]
NOTES: dict[PriceKind, str] = {
    "metered": "",
    "self_hosted": "self-hosted, not metered",
    "unknown": "unknown price",
}


@dataclass(frozen=True)
class Price:
    kind: PriceKind
    input: float | None = None  # USD per million input tokens
    output: float | None = None  # USD per million output tokens

    @property
    def note(self) -> str:
        return NOTES[self.kind]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "input_per_mtok": self.input, "output_per_mtok": self.output, "note": self.note}


def _parse_value(v: object) -> tuple[float, float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return float(v[0]), float(v[1])
    if isinstance(v, dict) and "input" in v and "output" in v:
        return float(v["input"]), float(v["output"])
    return None


@lru_cache(maxsize=8)
def _overrides(raw: str) -> dict[str, tuple[float, float]]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("FORGE_PRICES is not valid JSON; ignoring it")
        return {}
    out: dict[str, tuple[float, float]] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                parsed = _parse_value(v)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None:
                log.warning("FORGE_PRICES[%r] must be [input, output] or {input, output}; ignoring it", k)
                continue
            out[str(k)] = parsed
    return out


def overrides() -> dict[str, tuple[float, float]]:
    return _overrides(os.environ.get("FORGE_PRICES") or "")


def price_for(model: str | None) -> Price:
    """The price of a model string (`anthropic:<id>`, `openai_compat:<url>|<model>`, `fake:<scenario>`)."""
    model = model or ""
    kind, _, rest = model.partition(":")
    ov = overrides()
    if model in ov:
        return Price("metered", *ov[model])
    if kind == "anthropic":
        if rest in ov:
            return Price("metered", *ov[rest])
        if rest in ANTHROPIC_PRICES:
            return Price("metered", *ANTHROPIC_PRICES[rest])
        return Price("unknown")
    if kind in ("openai_compat", "fake"):
        return Price("self_hosted", 0.0, 0.0)
    return Price("unknown")


def cost(model: str | None, tokens_in: int, tokens_out: int) -> float | None:
    """USD for the given token counts, or None when the model's price is unknown.

    `tokens_in` is the uncached input count. Prompt-cache reads and writes (runs.cache_read_tokens /
    cache_write_tokens) are reported as separate counts and are not priced here.
    TODO: price cache tokens once their multipliers relative to the input price are documented in this repo.
    """
    p = price_for(model)
    if p.input is None or p.output is None:
        return None
    return ((tokens_in or 0) * p.input + (tokens_out or 0) * p.output) / 1_000_000
