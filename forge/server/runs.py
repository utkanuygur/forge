"""RunManager: executes agent runs as bounded background asyncio tasks."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable

import httpx

from forge import budget, db, governance
from forge.server import obslog
from forge.server.views import run_view
from forge.types import AgentVersion, Event, Executor, Message, RunResult

log = logging.getLogger("forge.runs")
STOPPED = "Stopped by user"


def _turns(run_id: str) -> int:
    """Model responses so far: each starts at the first assistant/tool_call event after tool results."""
    n, open_ = 0, False
    for e in db.list_events(run_id):
        if e["type"] in ("assistant", "tool_call") and not open_:
            n, open_ = n + 1, True
        elif e["type"] == "tool_result":
            open_ = False
    return n



def _usage_from_events(run_id: str) -> dict[str, int]:
    """Token usage of a run so far from its trace: each model turn reports its usage exactly once (on its text
    event, its first tool_call or its error event; narration notes carry none)."""
    out = {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for e in db.list_events(run_id):
        if e["type"] in ("assistant", "tool_call", "error"):
            for k in out:
                out[k] += int((e["payload"] or {}).get(k) or 0)
    return out


After = Callable[[Executor, RunResult], Awaitable[Any]]


class RunManager:
    def __init__(self, max_concurrency: int | None = None) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrency or int(os.environ.get("FORGE_MAX_CONCURRENCY") or 8))
        self.tasks: dict[str, asyncio.Task] = {}
        self.after_results: dict[str, Any] = {}
        self.approvals = governance.ApprovalRegistry()
        self.background: set[asyncio.Task] = set()
        self.cancelled: set[str] = set()

    def cancel(self, run_id: str) -> bool:
        """Stop a run started by this manager. Returns False if it is not running here.

        A run paused on an approval is cancelled the same way: the pending approval is resolved as denied
        (forge.governance.RunGovernor.before_tool) and the sandbox is stopped. Child runs started by its
        agent__ tools are stopped too."""
        task = self.tasks.get(run_id)
        if task is None or task.done() or run_id in self.cancelled:
            return False
        self.cancelled.add(run_id)
        task.cancel()
        for child in db.list_child_runs(run_id):
            if child["status"] in ("queued", "running", "awaiting_approval"):
                self.cancel(child["id"])
        return True

    async def start_run(
        self,
        agent: dict,
        version: dict,
        input: str,
        source: str,
        *,
        seed_overrides: dict[str, bytes] | None = None,
        callback_url: str | None = None,
        eval_case: dict | None = None,
        after: After | None = None,
        temperature: float | None = None,
        parent_run_id: str | None = None,
        max_seconds: int | None = None,
        session_id: str | None = None,
        turn_index: int | None = None,
        history: list[Message] | None = None,
        workspace: dict[str, bytes] | None = None,
        visitor_id: str | None = None,
    ) -> str:
        """`workspace`, when given, replaces the version's seed files (a session turn continuing from the
        previous turn's archive); `history` is the session's earlier conversation."""
        # Raises BudgetExceeded (HTTP 402). Child runs (agent__ tools) skip the concurrency semaphore below but
        # never this check, and their own version's per-run cap applies through the RunGovernor. Playground
        # turns go through it like every other run.
        budget.ensure_budget(db.get_agent(agent["id"]) or agent)
        run = db.create_run(agent["id"], version["id"], source, input,
                            eval_case_id=eval_case["id"] if eval_case else None, callback_url=callback_url,
                            parent_run_id=parent_run_id, session_id=session_id, turn_index=turn_index,
                            visitor_id=visitor_id)
        seed: dict[str, bytes] = {k: v.encode() for k, v in (version.get("seed_files") or {}).items()}
        if workspace is not None:
            seed = dict(workspace)
        if eval_case:
            seed.update({k: v.encode() for k, v in (eval_case.get("seed_files") or {}).items()})
        seed.update(seed_overrides or {})
        obslog.lifecycle("queued", run, agent["slug"])
        task = asyncio.create_task(self._guarded(run["id"], agent, version, input, seed, after, temperature, source,
                                                 parent_run_id=parent_run_id, max_seconds=max_seconds,
                                                 history=history))
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _t, rid=run["id"]: self.tasks.pop(rid, None))
        return run["id"]

    async def wait(self, run_id: str) -> dict | None:
        task = self.tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return db.get_run(run_id)

    async def _guarded(self, run_id: str, *args: Any, **kwargs: Any) -> None:
        """Runs `_execute`; a cancel before the run got a sandbox (still queued) is finalized here."""
        try:
            await self._execute(run_id, *args, **kwargs)
        except asyncio.CancelledError:
            if run_id not in self.cancelled:
                raise
            if not db.has_event(run_id, "run_finished"):
                db.append_event(run_id, db.next_seq(run_id), time.time(), "error", {"message": STOPPED})
                self._finalize(run_id, RunResult(status="cancelled", error=STOPPED))
        finally:
            self.cancelled.discard(run_id)

    async def _execute(self, run_id: str, agent: dict, version: dict, input: str, seed: dict[str, bytes],
                       after: After | None, temperature: float | None, source: str | None = None, *,
                       parent_run_id: str | None = None, max_seconds: int | None = None,
                       history: list[Message] | None = None) -> None:
        from forge.executors import make_executor
        from forge.loop import run_agent
        from forge.providers import make_provider

        # Child runs (agent__ tools) skip the semaphore: their parent already holds a slot and waits for them,
        # so queueing them could deadlock a full pool.
        async with contextlib.nullcontext() if parent_run_id else self.semaphore:
            executor: Executor | None = None
            result: RunResult | None = None
            try:
                executor = make_executor(os.environ.get("FORGE_EXECUTOR") or "docker",  # type: ignore[arg-type]
                                         network=bool(version.get("network")))
                secrets = db.get_secrets(agent["id"])
                # A cancel during start must not orphan a half-created sandbox: let start finish, then
                # the finally block below stops it.
                starting = asyncio.ensure_future(executor.start(seed, dict(secrets)))
                try:
                    await asyncio.shield(starting)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await starting
                    raise
                started = db.update_run(run_id, status="running", started_at=time.time())
                obslog.lifecycle("started", started or {"id": run_id}, agent["slug"])
                v = AgentVersion(**{k: version[k] for k in AgentVersion.model_fields if k in version})

                async def emit(e: Event) -> None:
                    db.append_event(run_id, e.seq, e.ts, e.type, e.payload)

                # An awaiting_approval run keeps its concurrency slot (and its sandbox) while it waits.
                hooks = governance.RunGovernor(run_id, agent, version, self.approvals, secrets,
                                               on_approval_requested=lambda ap: self._notify_approval(run_id, ap),
                                               source=source)
                from forge.tools import build_registry

                registry = build_registry(v, secrets=secrets, executor=executor, run_id=run_id,
                                          agent_id=agent["id"], manager=self)
                extra: dict[str, Any] = {"max_seconds": max_seconds} if max_seconds else {}
                if registry is not None:
                    extra["extra_tools"] = registry
                if history:
                    extra["history"] = history
                try:
                    result = await run_agent(v, input, run_id=run_id, provider=make_provider(v.model),
                                             executor=executor, emit=emit, secrets=secrets or None,
                                             temperature=temperature, hooks=hooks, **extra)
                except asyncio.CancelledError:
                    if run_id not in self.cancelled:
                        raise
                    # Stopped by the user: record it, keep the workspace so a session can continue from it.
                    # Usage comes from the per-turn fields already in the trace, so cost_usd is kept.
                    db.append_event(run_id, db.next_seq(run_id), time.time(), "error", {"message": STOPPED})
                    result = RunResult(status="cancelled", error=STOPPED, turns=_turns(run_id),
                                       **_usage_from_events(run_id))
                if after is not None and result.status != "cancelled":
                    try:
                        self.after_results[run_id] = await after(executor, result)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("after hook failed for %s", run_id)
                        self.after_results[run_id] = (False, f"checker error: {exc}")
                try:
                    out = db.run_dir(run_id)
                    out.mkdir(parents=True, exist_ok=True)
                    archive, diff = out / "workspace.tar.gz", out / "diff.patch"
                    await executor.export_workspace(str(archive), str(diff))
                    db.update_run(run_id, artifact_path=str(archive) if archive.exists() else None,
                                  diff_path=str(diff) if diff.exists() else None)
                except Exception:  # noqa: BLE001
                    log.exception("export failed for %s", run_id)
            except Exception as exc:  # noqa: BLE001
                log.exception("run %s failed", run_id)
                usage = result.model_dump(include={"turns", "tokens_in", "tokens_out", "cache_read_tokens",
                                                   "cache_write_tokens"}) if result else {}
                result = RunResult(status="failed", output=result.output if result else "",
                                   error=f"{type(exc).__name__}: {exc}", **usage)
                seq = db.next_seq(run_id)
                db.append_event(run_id, seq, time.time(), "error", {"message": result.error})
            finally:
                if executor is not None:
                    try:
                        await executor.stop()
                    except Exception:  # noqa: BLE001
                        log.exception("executor stop failed for %s", run_id)
            self._finalize(run_id, result or RunResult(status="failed", error="no result"))
        run = db.get_run(run_id)
        if run:
            obslog.finished(run, agent["slug"], version.get("model"))
        if run and run["callback_url"]:
            await post_webhook(run["callback_url"], run_view(run), agent_id=agent["id"])

    async def _notify_approval(self, run_id: str, approval: dict) -> None:
        """Posts the Run (status awaiting_approval) plus the pending `approval` to the run's callback_url."""
        run = db.get_run(run_id)
        if run and run["callback_url"]:
            t = asyncio.create_task(post_webhook(run["callback_url"], {**run_view(run), "approval": approval},
                                                 agent_id=run["agent_id"], event="run.awaiting_approval"))
            self.background.add(t)
            t.add_done_callback(self.background.discard)

    def _finalize(self, run_id: str, result: RunResult) -> None:
        # Writing the final token counts also re-prices runs.cost_usd (db.update_run), with the same function
        # the governor used after each turn; it matters for turns that ended the run before after_turn.
        db.update_run(run_id, status=result.status, output=result.output, error=result.error, turns=result.turns,
                      tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                      cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
                      ended_at=time.time())
        if not db.has_event(run_id, "run_finished"):
            db.append_event(run_id, db.next_seq(run_id), time.time(), "run_finished", {
                "status": result.status, "output": result.output, "turns": result.turns,
                "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                "cache_read_tokens": result.cache_read_tokens, "cache_write_tokens": result.cache_write_tokens})
        run = db.get_run(run_id)
        if run and run.get("parent_run_id"):
            # Roll this child's usage (its own plus its descendants', already rolled into it) into the parent's
            # child_* fields. The cost is the child's stored runs.cost_usd (one price source, see forge/budget.py).
            # The parent's own tokens and cost_usd never include children, so sums over runs do not double count.
            db.add_child_usage(run["parent_run_id"], run["tokens_in"] + run["child_tokens_in"],
                               run["tokens_out"] + run["child_tokens_out"],
                               float(run.get("cost_usd") or 0) + float(run.get("child_cost_usd") or 0))


WEBHOOK_SIGNATURE = "X-Forge-Signature-256"


def sign_webhook(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_webhook(url: str, body: dict, retries: int = 3, backoff: float = 1.0, *, agent_id: str | None = None,
                       event: str = "run.finished", transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Delivers a signed webhook through the SSRF-safe client of forge/tools/http.py (resolved addresses must be
    public, the connection is pinned to the checked IP and every redirect hop is re-checked;
    FORGE_ALLOW_PRIVATE_HTTP=1 opts out). Headers: X-Forge-Event, X-Forge-Delivery (one id for all retries) and
    X-Forge-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with the agent's webhook secret>."""
    from forge.tools.http import ToolError, send_request

    raw = json.dumps(body, separators=(",", ":"), default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "forge-webhooks", "X-Forge-Event": event,
               "X-Forge-Delivery": uuid.uuid4().hex}
    if agent_id:
        headers[WEBHOOK_SIGNATURE] = sign_webhook(db.webhook_secret(agent_id), raw)
    for attempt in range(retries + 1):
        try:
            status, *_ = await send_request("POST", url, headers=headers, params={}, json_body=None, timeout=10,
                                            content=raw, transport=transport)
            if status < 400:
                return True
        except ToolError as exc:  # a blocked or malformed destination never becomes reachable by retrying
            log.warning("webhook %s refused: %s", url, exc)
            return False
        except httpx.HTTPError:
            pass
        if attempt < retries:
            await asyncio.sleep(backoff * 2**attempt)
    log.warning("webhook %s failed after %d retries", url, retries)
    return False


manager: RunManager | None = None


def get_manager() -> RunManager:
    global manager
    if manager is None:
        manager = RunManager()
    return manager


def reset_manager() -> RunManager:
    global manager
    manager = RunManager()
    return manager
