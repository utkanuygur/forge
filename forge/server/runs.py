"""RunManager: executes agent runs as bounded background asyncio tasks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx

from forge import db
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

After = Callable[[Executor, RunResult], Awaitable[Any]]


class RunManager:
    def __init__(self, max_concurrency: int | None = None) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrency or int(os.environ.get("FORGE_MAX_CONCURRENCY") or 8))
        self.tasks: dict[str, asyncio.Task] = {}
        self.after_results: dict[str, Any] = {}
        self.cancelled: set[str] = set()

    def cancel(self, run_id: str) -> bool:
        """Stop a run started by this manager. Returns False if it is not running here."""
        task = self.tasks.get(run_id)
        if task is None or task.done() or run_id in self.cancelled:
            return False
        self.cancelled.add(run_id)
        task.cancel()
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
        session_id: str | None = None,
        turn_index: int | None = None,
        history: list[Message] | None = None,
        workspace: dict[str, bytes] | None = None,
    ) -> str:
        """`workspace`, when given, replaces the version's seed files (a session turn continuing from the
        previous turn's archive); `history` is the session's earlier conversation."""
        run = db.create_run(agent["id"], version["id"], source, input,
                            eval_case_id=eval_case["id"] if eval_case else None, callback_url=callback_url,
                            session_id=session_id, turn_index=turn_index)
        seed: dict[str, bytes] = {k: v.encode() for k, v in (version.get("seed_files") or {}).items()}
        if workspace is not None:
            seed = dict(workspace)
        if eval_case:
            seed.update({k: v.encode() for k, v in (eval_case.get("seed_files") or {}).items()})
        seed.update(seed_overrides or {})
        task = asyncio.create_task(self._guarded(run["id"], agent, version, input, seed, after, temperature, history))
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _t, rid=run["id"]: self.tasks.pop(rid, None))
        return run["id"]

    async def wait(self, run_id: str) -> dict | None:
        task = self.tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return db.get_run(run_id)

    async def _guarded(self, run_id: str, *args: Any) -> None:
        """Runs `_execute`; a cancel before the run got a sandbox (still queued) is finalized here."""
        try:
            await self._execute(run_id, *args)
        except asyncio.CancelledError:
            if run_id not in self.cancelled:
                raise
            if not db.has_event(run_id, "run_finished"):
                db.append_event(run_id, db.next_seq(run_id), time.time(), "error", {"message": STOPPED})
                self._finalize(run_id, RunResult(status="cancelled", error=STOPPED))
        finally:
            self.cancelled.discard(run_id)

    async def _execute(self, run_id: str, agent: dict, version: dict, input: str, seed: dict[str, bytes],
                       after: After | None, temperature: float | None, history: list[Message] | None = None) -> None:
        from forge.executors import make_executor
        from forge.loop import run_agent
        from forge.providers import make_provider

        async with self.semaphore:
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
                db.update_run(run_id, status="running", started_at=time.time())
                v = AgentVersion(**{k: version[k] for k in AgentVersion.model_fields if k in version})

                async def emit(e: Event) -> None:
                    db.append_event(run_id, e.seq, e.ts, e.type, e.payload)

                try:
                    result = await run_agent(v, input, run_id=run_id, provider=make_provider(v.model),
                                             executor=executor, emit=emit, secrets=secrets or None,
                                             temperature=temperature, **({"history": history} if history else {}))
                except asyncio.CancelledError:
                    if run_id not in self.cancelled:
                        raise
                    # Stopped by the user: record it, keep the workspace so a session can continue from it.
                    db.append_event(run_id, db.next_seq(run_id), time.time(), "error", {"message": STOPPED})
                    result = RunResult(status="cancelled", error=STOPPED, turns=_turns(run_id))
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
                result = RunResult(status="failed", output=result.output if result else "",
                                   turns=result.turns if result else 0, error=f"{type(exc).__name__}: {exc}")
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
        if run and run["callback_url"]:
            await post_webhook(run["callback_url"], run_view(run))

    def _finalize(self, run_id: str, result: RunResult) -> None:
        db.update_run(run_id, status=result.status, output=result.output, error=result.error, turns=result.turns,
                      tokens_in=result.tokens_in, tokens_out=result.tokens_out, ended_at=time.time())
        if not db.has_event(run_id, "run_finished"):
            db.append_event(run_id, db.next_seq(run_id), time.time(), "run_finished", {
                "status": result.status, "output": result.output, "turns": result.turns,
                "tokens_in": result.tokens_in, "tokens_out": result.tokens_out})


async def post_webhook(url: str, body: dict, retries: int = 3, backoff: float = 1.0) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(url, json=body)
                if r.status_code < 400:
                    return True
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
