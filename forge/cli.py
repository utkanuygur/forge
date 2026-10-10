"""`forge` command-line tool: run any agent version locally against a directory.

The loop runs on this machine with a LocalExecutor (or a local Docker sandbox with --sandbox).
Traces stream back to the server so local feedback feeds the improvement system; they never
count toward evaluation scores or promotion.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from forge.types import AgentVersion, Event

app = typer.Typer(add_completion=False, help="Run Forge agents locally.")

CONFIG_PATH = Path.home() / ".config" / "forge" / "config.json"
OUTBOX = Path.home() / ".config" / "forge" / "outbox"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _client(cfg: dict) -> httpx.Client:
    if not cfg.get("url"):
        raise typer.BadParameter("Not logged in. Run: forge login <url> <api-key>")
    return httpx.Client(
        base_url=cfg["url"].rstrip("/") + "/v1",
        headers={"Authorization": f"Bearer {cfg['key']}"},
        timeout=30,
    )


@app.command()
def login(url: str, key: str) -> None:
    """Save the server URL and an agent API key."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"url": url, "key": key}))
    CONFIG_PATH.chmod(0o600)
    typer.echo(f"Saved credentials for {url}")


class _Sync:
    """Uploads events in batches; buffers to a local JSONL outbox when the server is unreachable."""

    def __init__(self, client: httpx.Client | None, run_id: str) -> None:
        self.client, self.run_id, self.pending = client, run_id, []

    def add(self, event: Event) -> None:
        self.pending.append(event.model_dump())
        if event.type in ("tool_result", "run_finished", "error"):
            self.flush()

    def flush(self) -> None:
        if not self.pending or self.client is None:
            return
        try:
            self.client.post(f"/runs/{self.run_id}/events", json=self.pending).raise_for_status()
        except httpx.HTTPError:
            OUTBOX.mkdir(parents=True, exist_ok=True)
            with (OUTBOX / f"{self.run_id}.jsonl").open("a") as f:
                for e in self.pending:
                    f.write(json.dumps(e) + "\n")
        self.pending = []


def _print_event(e: Event) -> None:
    p = e.payload
    if e.type == "assistant" and p.get("text"):
        typer.secho(p["text"], fg="white")
    elif e.type == "tool_call":
        args = json.dumps(p.get("args", {}))
        typer.secho(f"→ {p.get('name')} {args[:200]}", fg="cyan")
    elif e.type == "tool_result" and p.get("is_error"):
        typer.secho(f"  ! {str(p.get('output', ''))[:300]}", fg="red")
    elif e.type == "error":
        typer.secho(f"error: {p.get('message')}", fg="red")


def _git_dirty(root: Path) -> bool:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


@app.command()
def run(
    target: str = typer.Argument(..., help="Agent slug, optionally pinned: repo-maintainer@3"),
    task: str = typer.Argument(..., help="What the agent should do"),
    dir: Path = typer.Option(Path("."), "--dir", help="Directory the agent works in"),
    auto: bool = typer.Option(False, "--auto", help="Do not ask before each command or write"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Run tools in a local Docker container"),
    model: str | None = typer.Option(None, "--model", help="Override the version's model"),
    no_sync: bool = typer.Option(False, "--no-sync", help="Keep the run entirely local"),
) -> None:
    """Run an agent version locally against DIR."""
    load_dotenv()
    from forge.executors import make_executor
    from forge.loop import run_agent
    from forge.providers import make_provider

    slug, _, pin = target.partition("@")
    cfg = _load_config()
    client = _client(cfg)
    version = AgentVersion(**client.get(f"/agents/{slug}/versions/{pin or 'prod'}").raise_for_status().json())
    if model:
        version = version.model_copy(update={"model": model})

    root = dir.resolve()
    if _git_dirty(root) and not typer.confirm(f"{root} has uncommitted changes. Continue?"):
        raise typer.Exit(1)

    if no_sync:
        run_id, sync_client = f"r_local{int(time.time())}", None
    else:
        body = {"agent_slug": slug, "version_n": version.n, "input": task, "source": "local"}
        run_id, sync_client = client.post("/runs", json=body).raise_for_status().json()["id"], client
    sync = _Sync(sync_client, run_id)

    def confirm(description: str) -> bool:
        return typer.confirm(f"Allow: {description}", default=True)

    if sandbox:
        executor = make_executor("docker", network=version.network)
    else:
        executor = make_executor("local", root=str(root), mode="auto" if auto else "ask", confirm=confirm)

    async def emit(e: Event) -> None:
        _print_event(e)
        sync.add(e)

    async def main() -> None:
        seed = {} if not sandbox else {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts and p.stat().st_size < 1_000_000
        }
        await executor.start(seed, dict(os.environ) if not sandbox else {})
        try:
            result = await run_agent(
                version, task, run_id=run_id, provider=make_provider(version.model),
                executor=executor, emit=emit,
            )
        finally:
            await executor.stop()
        sync.flush()
        typer.secho(f"\n{result.status} · {result.turns} turns · run {run_id}", bold=True)
        if result.output:
            typer.echo(result.output)

    asyncio.run(main())


@app.command()
def feedback(run_id: str, rating: str = typer.Argument(..., help="up or down"), comment: str = "") -> None:
    """Rate a run so the next improvement cycle can learn from it."""
    if rating not in ("up", "down"):
        raise typer.BadParameter("rating must be 'up' or 'down'")
    client = _client(_load_config())
    client.post(f"/runs/{run_id}/feedback", json={"rating": 1 if rating == "up" else -1, "comment": comment}).raise_for_status()
    typer.echo("Feedback recorded.")


@app.command()
def sync() -> None:
    """Upload traces buffered while offline."""
    client = _client(_load_config())
    for path in sorted(OUTBOX.glob("*.jsonl")) if OUTBOX.exists() else []:
        events = [json.loads(line) for line in path.read_text().splitlines() if line]
        client.post(f"/runs/{path.stem}/events", json=events).raise_for_status()
        path.unlink()
        typer.echo(f"Synced {path.stem} ({len(events)} events)")


if __name__ == "__main__":
    app()
