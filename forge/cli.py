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
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx
import typer
from dotenv import load_dotenv

from forge.cli_render import Transcript
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

    printer = Transcript()  # the same renderer as `forge chat`
    printer.run_id = run_id

    async def emit(e: Event) -> None:
        printer.event(e)
        sync.add(e)

    async def main() -> None:
        seed = {} if not sandbox else {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts and p.stat().st_size < 1_000_000
        }
        await executor.start(seed, dict(os.environ) if not sandbox else {})
        printer.start_turn()
        # HTTP tools and MCP servers work locally; {{NAME}} references resolve from this machine's environment.
        # Callable agents need the server's run manager, so they are not offered in local runs.
        from forge.tools import build_registry
        from forge.tools.http import SECRET_RE

        refs = {m for t in version.http_tools for v in t.headers.values() for m in SECRET_RE.findall(v)}
        refs |= {m for sv in version.mcp_servers for v in [*sv.headers.values(), *sv.env.values()]
                 for m in SECRET_RE.findall(v)}
        local_secrets = {k: os.environ[k] for k in refs if os.environ.get(k)}
        registry = build_registry(version, secrets=local_secrets, executor=executor)
        try:
            result = await run_agent(
                version, task, run_id=run_id, provider=make_provider(version.model),
                executor=executor, emit=emit, secrets=local_secrets or None, extra_tools=registry,
            )
        finally:
            printer.end_turn()  # run_finished already printed the final answer and the footer
            await executor.stop()
        sync.flush()

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


# ---------------------------------------------------------------- chat (sessions API)

CHAT_HISTORY = Path.home() / ".config" / "forge" / "chat_history"

CHAT_HELP = """Commands:
  /new            start a new session with the same agent
  /files          list the latest turn's workspace files (changed files marked)
  /open <path>    print a file from the latest turn's workspace
  /help           show this help
  /exit           leave (Ctrl+D also works)
Ctrl+C stops a running turn; press it again at the prompt to leave."""


def parse_target(target: str) -> tuple[str, int | None]:
    """`slug`, `slug@3` or `slug@v3` to (slug, version or None)."""
    slug, _, pin = target.partition("@")
    pin = pin.removeprefix("v")
    if pin and not pin.isdigit():
        raise typer.BadParameter("version must look like slug@3 or slug@v3")
    return slug, int(pin) if pin else None


def iter_sse(resp: httpx.Response):
    """Yields (event, id, data) for each server-sent event message; comments and pings are skipped."""
    event, ident, data = "message", None, []
    for line in resp.iter_lines():
        line = line.rstrip("\r")
        if not line:
            if data or event != "message":
                yield event, ident, "\n".join(data)
            event, ident, data = "message", None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "id":
            ident = value
        elif field == "data":
            data.append(value)
    if data:
        yield event, ident, "\n".join(data)


class ChatExit(Exception):
    """Leaves the chat loop."""


class Chat:
    """The `forge chat` REPL over the sessions API. `read_line` is `input` in the terminal and a stub in tests."""

    def __init__(self, client: httpx.Client, slug: str, version: int | None, view: Any,
                 read_line: Callable[[str], str] = input) -> None:
        self.client, self.slug, self.version, self.view, self.read_line = client, slug, version, view, read_line
        self.session: dict | None = None
        self.latest_run: str | None = None
        self.armed_exit = False  # a Ctrl+C just happened; the next one leaves

    # ------------------------------------------------ http helpers

    def _detail(self, r: httpx.Response) -> str:
        try:
            body = r.json()
            return str(body.get("detail") or r.text) if isinstance(body, dict) else r.text
        except ValueError:
            return r.text or f"HTTP {r.status_code}"

    def _ok(self, r: httpx.Response) -> dict | list:
        if r.status_code >= 400:
            raise RuntimeError(self._detail(r))
        return r.json()

    # ------------------------------------------------ sessions

    def new_session(self) -> None:
        body: dict[str, Any] = {}
        if self.version is not None:
            body["version"] = self.version
        self.session = self._ok(self.client.post(f"/agents/{self.slug}/sessions", json=body))  # type: ignore[assignment]
        self.latest_run = None
        self._banner()

    def resume(self, session_id: str) -> None:
        s = self._ok(self.client.get(f"/sessions/{session_id}"))
        assert isinstance(s, dict)
        if s.get("agent_slug") and s["agent_slug"] != self.slug:
            self.view.info(f"Session {session_id} belongs to {s['agent_slug']}; continuing with that agent.")
            self.slug = s["agent_slug"]
        self.session = s
        self._banner()
        turns = s.get("turns") or []
        for t in turns:
            self.view.write(self.view.style("> " + str(t.get("input") or ""), "dim"))
            if t.get("output"):
                self.view.write(self.view.style("● " + str(t["output"]).strip().splitlines()[0][:200], "dim"))
            self.view.write()
        self.latest_run = turns[-1]["id"] if turns else None

    def _banner(self) -> None:
        s = self.session or {}
        n = s.get("version_n")
        ver = f"v{n}" if n is not None else ("draft" if s.get("draft") else "prod")
        self.view.write(self.view.style("Forge chat", "bold") + self.view.style(
            f" · {self.slug} {ver} · session {s.get('id')}", "dim"))
        self.view.info("Type a message. /help for commands, Ctrl+D to exit.")
        self.view.write()

    # ------------------------------------------------ turns

    def send(self, text: str) -> None:
        assert self.session is not None
        r = self.client.post(f"/sessions/{self.session['id']}/messages", json={"input": text})
        if r.status_code == 409:
            self.view.error("A turn is still running. Wait for it to finish or press Ctrl+C to stop it.")
            return
        run = self._ok(r)
        assert isinstance(run, dict)
        self.latest_run = run["id"]
        self.view.write()
        self.view.start_turn()
        try:
            self.stream(run["id"])
        except KeyboardInterrupt:
            self.view.end_turn()
            self.cancel(run["id"])
            return
        finally:
            self.view.end_turn()

    def stream(self, run_id: str) -> None:
        """Renders the turn's events until `event: end`; reconnects after dropped connections."""
        last, finished, attempts = -1, False, 0
        while True:
            try:
                with self.client.stream("GET", f"/runs/{run_id}/events", params={"after": last},
                                        timeout=httpx.Timeout(30, read=90)) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise RuntimeError(self._detail(resp))
                    for event, ident, data in iter_sse(resp):
                        if event == "end":
                            break
                        try:
                            e = json.loads(data)
                        except ValueError:
                            continue
                        if ident and ident.lstrip("-").isdigit():
                            last = int(ident)
                        finished = finished or e.get("type") == "run_finished"
                        self.view.event(e)
                break
            except (httpx.TransportError, httpx.StreamError):
                attempts += 1
                if attempts > 3:
                    self.view.error("Lost the connection to the server while streaming this turn.")
                    return
                time.sleep(0.5 * attempts)
        if not finished:  # stream ended without run_finished (older server or a cancelled turn)
            run = self.client.get(f"/runs/{run_id}")
            if run.status_code < 400:
                self.view.footer(run.json())

    def cancel(self, run_id: str) -> None:
        try:
            r = self.client.post(f"/runs/{run_id}/cancel")
        except KeyboardInterrupt:
            raise ChatExit from None
        except httpx.HTTPError as exc:
            self.view.error(f"Could not stop the turn: {exc}")
            return
        if r.status_code >= 400:
            self.view.error(f"Could not stop the turn: {self._detail(r)}")
            return
        self.view.interrupted()
        self.armed_exit = True

    # ------------------------------------------------ workspace

    def files(self) -> None:
        if not self.latest_run:
            self.view.info("No turns yet.")
            return
        r = self.client.get(f"/runs/{self.latest_run}/files", params={"changed": 1})
        if r.status_code == 404:
            self.view.info("This turn has no saved workspace yet.")
            return
        self.view.files(self._ok(r))  # type: ignore[arg-type]

    def open(self, path: str) -> None:
        if not path:
            self.view.error("Usage: /open <path>")
            return
        if not self.latest_run:
            self.view.info("No turns yet.")
            return
        quoted = "/".join(quote(p, safe="") for p in path.strip().removeprefix("./").split("/"))
        r = self.client.get(f"/runs/{self.latest_run}/files/{quoted}")
        if r.status_code == 415:
            self.view.info(f"{path} is a binary file and is not shown.")
        elif r.status_code == 413:
            self.view.info(f"{path} is larger than 1 MB and is not shown.")
        elif r.status_code >= 400:
            self.view.error(self._detail(r))
        else:
            self.view.file(path, r.text)

    # ------------------------------------------------ loop

    def command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        if cmd in ("/exit", "/quit"):
            raise ChatExit
        if cmd == "/help":
            self.view.write(CHAT_HELP)
        elif cmd == "/new":
            self.new_session()
        elif cmd == "/files":
            self.files()
        elif cmd == "/open":
            self.open(arg.strip())
        else:
            self.view.error(f"Unknown command {cmd}. Type /help for the list.")

    def loop(self) -> None:
        while True:
            try:
                line = self.read_line(self.view.style("> ", "bold"))
            except EOFError:
                self.view.write()
                return
            except KeyboardInterrupt:
                if self.armed_exit:
                    self.view.write()
                    return
                self.armed_exit = True
                self.view.write()
                self.view.info("Press Ctrl+C again to exit.")
                continue
            self.armed_exit = False
            line = line.strip()
            if not line:
                continue
            try:
                if line.startswith("/"):
                    self.command(line)
                else:
                    self.send(line)
            except ChatExit:
                return
            except KeyboardInterrupt:  # Ctrl+C outside a streaming turn (for example while sending)
                self.view.write()
                self.view.info("Interrupted. Press Ctrl+C again to exit.")
                self.armed_exit = True
            except (RuntimeError, httpx.HTTPError) as exc:
                self.view.error(f"Error: {exc}")

    def run(self, session_id: str | None = None) -> None:
        try:
            if session_id:
                self.resume(session_id)
            else:
                self.new_session()
        except (RuntimeError, httpx.HTTPError) as exc:
            self.view.error(f"Error: {exc}")
            raise typer.Exit(1) from None
        self.loop()


def _chat_client(url: str | None) -> httpx.Client:
    cfg = _load_config()
    if url and url.rstrip("/") != (cfg.get("url") or "").rstrip("/"):
        cfg = {"url": url}  # another server: builder access from localhost, no key
    if not cfg.get("url"):
        raise typer.BadParameter("Not logged in. Run: forge login <url> <api-key>, or pass --url")
    headers = {"Authorization": f"Bearer {cfg['key']}"} if cfg.get("key") else {}
    return httpx.Client(base_url=cfg["url"].rstrip("/") + "/v1", headers=headers, timeout=30)


def _readline_input() -> Callable[[str], str]:
    """`input` with persistent history when readline is available and stdin is a terminal."""
    if not sys.stdin.isatty():
        return input
    try:
        import atexit
        import readline
    except ImportError:
        return input
    try:
        CHAT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        if CHAT_HISTORY.exists():
            readline.read_history_file(str(CHAT_HISTORY))
        readline.set_history_length(1000)
        atexit.register(lambda: readline.write_history_file(str(CHAT_HISTORY)))
    except OSError:
        pass
    return input


@app.command()
def chat(
    target: str = typer.Argument(..., help="Agent slug, optionally pinned: repo-maintainer@3"),
    session: str | None = typer.Option(None, "--session", help="Resume an existing session by id"),
    url: str | None = typer.Option(None, "--url", envvar="FORGE_URL", help="Server URL (default: forge login)"),
) -> None:
    """Chat with an agent: a multi-turn session with a live transcript."""
    from forge.cli_render import Transcript

    slug, version = parse_target(target)
    with _chat_client(url) as client:
        Chat(client, slug, version, Transcript(), _readline_input()).run(session)


if __name__ == "__main__":
    app()
