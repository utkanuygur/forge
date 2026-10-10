"""Executors: where tool calls run. DockerExecutor (sandboxed container) and LocalExecutor (host directory)."""

from __future__ import annotations

import asyncio
import inspect
import os
import posixpath
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from forge.types import ExecResult, Executor

Confirm = Callable[[str], "bool | Awaitable[bool]"]

STREAM_CAP = 256_000  # bytes kept per stream (head + tail) so runaway output can't exhaust memory
LIST_CAP = 5000
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


async def _read_capped(stream: asyncio.StreamReader | None, cap: int = STREAM_CAP) -> str:
    if stream is None:
        return ""
    head, tail, dropped = bytearray(), bytearray(), 0
    half = cap // 2
    while chunk := await stream.read(65536):
        room = half - len(head)
        if room > 0:
            head += chunk[:room]
            chunk = chunk[room:]
        tail += chunk
        if len(tail) > half:
            dropped += len(tail) - half
            del tail[: len(tail) - half]
    mid = f"\n[... {dropped} bytes dropped ...]\n".encode() if dropped else b""
    return (bytes(head) + mid + bytes(tail)).decode("utf-8", "replace")


async def _run(
    argv: list[str],
    *,
    input: bytes | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdout_file: Any = None,
) -> ExecResult:
    """Run a process; on timeout kill its whole process group and return exit 124."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
        stdout=stdout_file if stdout_file is not None else asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    async def communicate() -> tuple[str, str]:
        if input is not None:
            assert proc.stdin
            proc.stdin.write(input)
            await proc.stdin.drain()
            proc.stdin.close()
        out, err = await asyncio.gather(
            _read_capped(proc.stdout) if stdout_file is None else asyncio.sleep(0, ""), _read_capped(proc.stderr)
        )
        await proc.wait()
        return out or "", err

    task = asyncio.ensure_future(communicate())
    try:
        out, err = await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out, err = await asyncio.wait_for(task, 5)
        except TimeoutError:
            task.cancel()
            out, err = "", ""
        return ExecResult(exit_code=124, stdout=out, stderr=err, timed_out=True)
    return ExecResult(exit_code=proc.returncode or 0, stdout=out, stderr=err)


def _check(res: ExecResult, what: str) -> ExecResult:
    if res.exit_code != 0:
        raise RuntimeError(f"{what} failed (exit {res.exit_code}): {res.stderr.strip() or res.stdout.strip()}")
    return res


WORKSPACE_ROOT = "/workspace"


def safe_relpath(path: str) -> str:
    """Normalize a workspace-relative path; reject absolute paths and anything escaping the root.

    A leading `/workspace` (the sandbox's absolute workspace root, which models often pass) is accepted and
    treated as relative: `/workspace/src/a.py` is `src/a.py` and `/workspace` is the root."""
    if not isinstance(path, str) or "\x00" in path:
        raise ValueError("invalid path")
    p = path.strip() or "."
    if p == WORKSPACE_ROOT or p.startswith(WORKSPACE_ROOT + "/"):
        p = p[len(WORKSPACE_ROOT):].lstrip("/") or "."
    if p.startswith("/") or p.startswith("~"):
        raise ValueError(f"path must be relative to the workspace root: {path!r}")
    norm = posixpath.normpath(p)
    if norm == ".." or norm.startswith("../"):
        raise ValueError(f"path escapes the workspace: {path!r}")
    return norm


GIT_ID = ["-c", "user.name=forge", "-c", "user.email=forge@localhost"]


# ---------------------------------------------------------------- docker


class DockerExecutor:
    def __init__(self, *, network: bool = False, image: str | None = None):
        self.network = network
        from forge import settings

        self.image = image or settings.get("sandbox_image") or "forge-sandbox"
        self.cpus = f"{float(settings.get('sandbox_cpus') or 1):g}"
        self.memory = str(settings.get("sandbox_memory") or "1g")
        self.container: str | None = None

    async def _exec(self, args: list[str], *, input: bytes | None = None, timeout: float | None = 60) -> ExecResult:
        if not self.container:
            raise RuntimeError("executor not started")
        flags = ["-i"] if input is not None else []
        return await _run(["docker", "exec", *flags, self.container, *args], input=input, timeout=timeout)

    async def start(self, seed_files: dict[str, bytes], env: dict[str, str]) -> None:
        argv = ["docker", "run", "-d", "--network", "bridge" if self.network else "none"]
        argv += ["--cpus", self.cpus, "--memory", self.memory, "--pids-limit", "256", "--user", "agent", "-w", "/workspace"]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        argv += [self.image, "sleep", "infinity"]
        self.container = _check(await _run(argv, timeout=120), "docker run").stdout.strip()
        for path, content in seed_files.items():
            await self._write_bytes(path, content if isinstance(content, bytes) else str(content).encode())
        script = (
            "git init -q && git config user.name forge && git config user.email forge@localhost"
            " && git add -A && git commit -q --allow-empty -m baseline && git tag baseline"
        )
        _check(await self._exec(["bash", "-lc", script]), "git baseline")

    async def _write_bytes(self, path: str, data: bytes) -> None:
        rel = safe_relpath(path)
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        _check(await self._exec(["sh", "-c", script, "sh", rel], input=data), f"write {rel}")

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        # In-container `timeout` makes sure the process really dies; the host-side timeout is a backstop.
        res = await self._exec(["timeout", "--kill-after=2", str(timeout), "bash", "-lc", cmd], timeout=timeout + 10)
        if res.exit_code == 124:
            res.timed_out = True
        return res

    async def read(self, path: str) -> str:
        rel = safe_relpath(path)
        res = await self._exec(["cat", "--", rel])
        if res.exit_code != 0:
            raise FileNotFoundError(res.stderr.strip() or f"cannot read {rel}")
        return res.stdout

    async def write(self, path: str, content: str) -> None:
        await self._write_bytes(path, content.encode())

    async def list(self, path: str = ".") -> list[str]:
        rel = safe_relpath(path)
        res = await self._exec(["find", rel, "-path", "*/.git", "-prune", "-o", "-type", "f", "-print"])
        if res.exit_code != 0:
            raise FileNotFoundError(res.stderr.strip() or f"cannot list {rel}")
        return sorted(posixpath.normpath(p) for p in res.stdout.splitlines() if p)[:LIST_CAP]

    async def export_workspace(self, archive_path: str, diff_path: str) -> None:
        for p in (archive_path, diff_path):
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "wb") as f:
            _check(
                await _run(
                    ["docker", "exec", self.container or "", "tar", "czf", "-", "-C", "/workspace", "."],
                    timeout=300,
                    stdout_file=f,
                ),
                "export archive",
            )
        res = _check(
            await self._exec(["bash", "-lc", "git add -A && git diff --cached --binary baseline"], timeout=120),
            "export diff",
        )
        Path(diff_path).write_text(res.stdout)

    async def stop(self) -> None:
        cid, self.container = self.container, None
        if cid:
            await _run(["docker", "rm", "-f", cid], timeout=60)


# ---------------------------------------------------------------- local


class LocalExecutor:
    def __init__(self, root: str | None = None, mode: Literal["ask", "auto"] = "auto", confirm: Confirm | None = None):
        self._owned = root is None
        self.root = Path(root or tempfile.mkdtemp(prefix="forge-ws-")).resolve()
        self.mode = mode
        self.confirm = confirm
        self.env: dict[str, str] = {}
        self.baseline = EMPTY_TREE

    async def _allowed(self, description: str) -> bool:
        if self.mode != "ask":
            return True
        if self.confirm is None:
            return False
        ok = self.confirm(description)
        return bool(await ok) if inspect.isawaitable(ok) else bool(ok)

    def _resolve(self, path: str) -> Path:
        rel = safe_relpath(path)
        full = (self.root / rel).resolve()
        if full != self.root and self.root not in full.parents:
            raise ValueError(f"path escapes the workspace: {path!r}")
        return full

    async def _git(self, *args: str, env: dict[str, str] | None = None) -> ExecResult:
        return await _run(["git", *GIT_ID, *args], cwd=str(self.root), timeout=120, env=env)

    async def start(self, seed_files: dict[str, bytes], env: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.env = dict(env)
        for path, content in seed_files.items():
            full = self._resolve(path)
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content if isinstance(content, bytes) else str(content).encode())
        inside = await self._git("rev-parse", "--is-inside-work-tree")
        if inside.exit_code == 0 and inside.stdout.strip() == "true":
            head = await self._git("rev-parse", "HEAD")
            self.baseline = head.stdout.strip() if head.exit_code == 0 else EMPTY_TREE
            return
        _check(await self._git("init", "-q"), "git init")
        await self._git("config", "user.name", "forge")
        await self._git("config", "user.email", "forge@localhost")
        _check(await self._git("add", "-A"), "git add")
        _check(await self._git("commit", "-q", "--allow-empty", "-m", "baseline"), "git commit")
        await self._git("tag", "baseline")
        self.baseline = _check(await self._git("rev-parse", "HEAD"), "git rev-parse").stdout.strip()

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        if not await self._allowed(f"Run command in {self.root}:\n{cmd}"):
            return ExecResult(exit_code=126, stdout="", stderr="permission denied: the user declined this command")
        return await _run(["bash", "-lc", cmd], cwd=str(self.root), timeout=timeout, env={**os.environ, **self.env})

    async def read(self, path: str) -> str:
        return self._resolve(path).read_text(errors="replace")

    async def write(self, path: str, content: str) -> None:
        full = self._resolve(path)
        if not await self._allowed(f"Write {len(content)} chars to {full}"):
            raise PermissionError(f"the user declined writing {path}")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    async def list(self, path: str = ".") -> list[str]:
        base = self._resolve(path)
        if not base.exists():
            raise FileNotFoundError(path)
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                out.append(str((Path(dirpath) / name).relative_to(self.root)))
                if len(out) >= LIST_CAP:
                    return out
        return out

    async def export_workspace(self, archive_path: str, diff_path: str) -> None:
        for p in (archive_path, diff_path):
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        _check(
            await _run(["tar", "czf", str(Path(archive_path).resolve()), "-C", str(self.root), "."], timeout=300),
            "export archive",
        )
        # Use a throwaway index so a user's own staging area is never touched.
        idx = _check(await self._git("rev-parse", "--git-path", "index"), "git index path").stdout.strip()
        idx_path = Path(idx) if Path(idx).is_absolute() else self.root / idx
        with tempfile.TemporaryDirectory() as tmp:
            tmp_idx = Path(tmp) / "index"
            if idx_path.exists():
                shutil.copy2(idx_path, tmp_idx)  # keep the mtime: git trusts it to spot same-second edits
            env = {**os.environ, "GIT_INDEX_FILE": str(tmp_idx)}
            _check(await self._git("add", "-A", ".", env=env), "git add")
            res = _check(await self._git("diff", "--cached", "--binary", self.baseline, "--", ".", env=env), "git diff")
        Path(diff_path).write_text(res.stdout)

    async def stop(self) -> None:
        # Only the temp dir this executor created is removed; a caller-provided root is never deleted.
        if self._owned and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def make_executor(
    kind: Literal["docker", "local"],
    *,
    network: bool = False,
    root: str | None = None,
    mode: Literal["ask", "auto"] = "auto",
    confirm: Confirm | None = None,
) -> Executor:
    if kind == "docker":
        return DockerExecutor(network=network)
    if kind == "local":
        return LocalExecutor(root=root, mode=mode, confirm=confirm)
    raise ValueError(f"unknown executor kind {kind!r}")
