"""Structured run lifecycle logs: one JSON line per event (queued, started, finished) on logger `forge.lifecycle`.

Enabled with FORGE_LOG_JSON=1, disabled with FORGE_LOG_JSON=0; when unset it is on unless stderr is a TTY.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

log = logging.getLogger("forge.lifecycle")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": round(record.created, 3), "level": record.levelname.lower(),
                               "logger": record.name, "msg": record.getMessage()}
        out.update(getattr(record, "forge", {}) or {})
        return json.dumps(out, default=str)


def enabled() -> bool:
    flag = os.environ.get("FORGE_LOG_JSON")
    if flag is not None and flag != "":
        return flag == "1"
    return not sys.stderr.isatty()


def configure() -> None:
    """Attaches a JSON stderr handler to `forge.lifecycle` once, if enabled."""
    if getattr(log, "_forge_configured", False):
        return
    log._forge_configured = True  # type: ignore[attr-defined]
    log.propagate = False
    if enabled():
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    else:
        log.addHandler(logging.NullHandler())


def lifecycle(event: str, run: dict, agent_slug: str, **fields: Any) -> None:
    """Logs one lifecycle event for a run. Never raises."""
    try:
        configure()
        if not log.handlers or isinstance(log.handlers[0], logging.NullHandler):
            return
        data: dict[str, Any] = {"event": f"run_{event}", "run_id": run.get("id"), "agent": agent_slug,
                                "source": run.get("source"), "version_id": run.get("version_id")}
        data.update(fields)
        log.info("run %s", event, extra={"forge": data})
    except Exception:  # noqa: BLE001
        pass


def finished(run: dict, agent_slug: str, model: str | None = None) -> None:
    """`model` is unused: the cost logged is the stored runs.cost_usd."""
    started, ended = run.get("started_at"), run.get("ended_at") or time.time()
    lifecycle("finished", run, agent_slug, status=run.get("status"),
              duration_ms=round((ended - started) * 1000, 1) if started else None,
              tokens_in=run.get("tokens_in"), tokens_out=run.get("tokens_out"),
              cost_usd=run.get("cost_usd"),
              error=run.get("error"))
