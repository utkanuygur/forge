"""RunManager: executes agent runs as bounded background asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx

from forge import db
from forge.server.views import run_view
from forge.types import AgentVersion, Event, Executor, RunResult

log = logging.getLogger("forge.runs")

After = Callable[[Executor, RunResult], Awaitable[Any]]


class RunManager:
    def __init__(self, max_concurrency: int | None = None) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrency or int(os.environ.get("FORGE_MAX_CONCURRENCY") or 8))
        self.tasks: dict[str, asyncio.Task] = {}
        self.after_results: dict[str, Any] = {}

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
    ) -> str:
        run = db.create_run(agent["id"], version["id"], source, input,
                            eval_case_id=eval_case["id"] if eval_case else None, callback_url=callback_url)
        seed: dict[str, bytes] = {k: v.encode() for k, v in (version.get("seed_files") or {}).items()}
        if eval_case:
            seed.update({k: v.encode() for k, v in (eval_case.get("seed_files") or {}).items()})
        seed.update(seed_overrides or {})
        task = asyncio.create_task(self._execute(run["id"], agent, version, input, seed, after, temperature))
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _t, rid=run["id"]: self.tasks.pop(rid, None))
        return run["id"]

    async def wait(self, run_id: str) -> dict | None:
        task = self.tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return db.get_run(run_id)

    async def _execute(self, run_id: str, agent: dict, version: dict, input: str, seed: dict[str, bytes],
                       after: After | None, temperature: float | None) -> None:
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
                await executor.start(seed, dict(secrets))
                db.update_run(run_id, status="running", started_at=time.time())
                v = AgentVersion(**{k: version[k] for k in AgentVersion.model_fields if k in version})

                async def emit(e: Event) -> None:
                    db.append_event(run_id, e.seq, e.ts, e.type, e.payload)

                result = await run_agent(v, input, run_id=run_id, provider=make_provider(v.model), executor=executor,
                                         emit=emit, secrets=secrets or None, temperature=temperature)
                if after is not None:
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
                      tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                      cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
                      ended_at=time.time())
        if not db.has_event(run_id, "run_finished"):
            db.append_event(run_id, db.next_seq(run_id), time.time(), "run_finished", {
                "status": result.status, "output": result.output, "turns": result.turns,
                "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                "cache_read_tokens": result.cache_read_tokens, "cache_write_tokens": result.cache_write_tokens})


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
