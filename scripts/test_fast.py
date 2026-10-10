#!/usr/bin/env python3
"""Run Forge's independent deterministic test layers concurrently and summarize them.

Each layer is a subprocess in its own process group. Its output streams to this terminal with a
`[layer]` prefix and is also saved to <log dir>/<layer>.log. At the end a table lists every layer's
status and wall time; the exit code is non-zero if any layer failed.

    uv run python scripts/test_fast.py                     # every layer
    uv run python scripts/test_fast.py --only python,web-unit
    uv run python scripts/test_fast.py --skip browser --quiet

The layers do not interfere: pytest workers use their own data dirs, ports and sandbox container
label, the browser suite's server picks a free port, and the demo checkers use throwaway workspaces.
Stdlib only, so it runs under any Python 3.10+.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COLORS = ["36", "35", "33", "34", "32", "96"]


def default_workers() -> int:
    return min(os.cpu_count() or 1, 6)


@dataclass
class Layer:
    name: str
    argv: list[str]
    cwd: Path = REPO
    proc: subprocess.Popen | None = None
    rc: int | None = None
    started: float = 0.0
    seconds: float = 0.0
    log: Path | None = None
    lines: list[str] = field(default_factory=list)


def layers(workers: str) -> list[Layer]:
    return [
        Layer("python", ["uv", "run", "pytest", "-q", "-m", "not live", "-n", workers, "--dist", "loadgroup"]),
        Layer("web-unit", ["npm", "test", "--silent"], REPO / "web"),
        Layer("browser", ["npm", "run", "--silent", "test:e2e"], REPO / "web"),
        Layer("demo", ["uv", "run", "python", "demo/verify.py"]),
        Layer("examples", ["uv", "run", "python", "demo/verify_examples.py"]),
    ]


def pump(layer: Layer, color: str, quiet: bool, lock: threading.Lock) -> None:
    assert layer.proc and layer.proc.stdout and layer.log
    prefix = f"\033[{color}m[{layer.name}]\033[0m " if sys.stdout.isatty() else f"[{layer.name}] "
    with layer.log.open("w") as f:
        for line in layer.proc.stdout:
            f.write(line)
            layer.lines.append(line)
            if not quiet:
                with lock:
                    sys.stdout.write(prefix + line if line.endswith("\n") else prefix + line + "\n")
                    sys.stdout.flush()
    layer.rc = layer.proc.wait()
    layer.seconds = time.monotonic() - layer.started


def fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}m{seconds % 60:04.1f}s" if seconds >= 60 else f"{seconds:.1f}s"


def run_layers(selected: list[Layer], *, quiet: bool = False, log_dir: Path | None = None,
               title: str = "test-fast") -> int:
    """Runs the layers concurrently, streams prefixed output, prints the summary; returns the exit code."""
    if not selected:
        print(f"{title}: nothing to run")
        return 0
    log_dir = log_dir or Path(tempfile.mkdtemp(prefix="forge-test-fast-"))
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "FORCE_COLOR": os.environ.get("FORCE_COLOR", "0"), "PYTHONUNBUFFERED": "1"}
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    t0 = time.monotonic()
    print(f"{title}: {', '.join(layer.name for layer in selected)} in parallel; logs in {log_dir}", flush=True)

    def stop_all(*_: object) -> None:
        for layer in selected:
            if layer.proc and layer.proc.poll() is None:
                try:
                    os.killpg(layer.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, lambda *a: (stop_all(), sys.exit(143)))
    try:
        for i, layer in enumerate(selected):
            layer.log = log_dir / f"{layer.name}.log"
            layer.started = time.monotonic()
            layer.proc = subprocess.Popen(layer.argv, cwd=layer.cwd, env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                          errors="replace", bufsize=1, start_new_session=True)
            t = threading.Thread(target=pump, args=(layer, COLORS[i % len(COLORS)], quiet, lock), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop_all()
        print("\ninterrupted", file=sys.stderr)
        return 130
    total = time.monotonic() - t0

    failed = [layer for layer in selected if layer.rc != 0]
    for layer in failed:
        print(f"\n---- {layer.name} failed (exit {layer.rc}); last 40 lines of {layer.log} ----")
        print("".join(layer.lines[-40:]), end="")
    width = max(len(layer.name) for layer in selected)
    print(f"\n{'layer'.ljust(width)}  status  time      command")
    for layer in selected:
        status = "PASS" if layer.rc == 0 else "FAIL"
        cmd = " ".join(layer.argv) + ("" if layer.cwd == REPO else f"  (in {layer.cwd.relative_to(REPO)}/)")
        print(f"{layer.name.ljust(width)}  {status:<6}  {fmt(layer.seconds):<8}  {cmd}")
    print(f"{'total'.ljust(width)}  {'FAIL' if failed else 'PASS':<6}  {fmt(total):<8}  (wall clock, layers in parallel)")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma separated layers to run")
    ap.add_argument("--skip", help="comma separated layers to leave out")
    ap.add_argument("--workers", default=os.environ.get("PYTEST_WORKERS") or str(default_workers()),
                    help="pytest-xdist workers for the python layer (default: $PYTEST_WORKERS or min(cpus, 6))")
    ap.add_argument("--quiet", action="store_true", help="only print the summary and the tail of failed layers")
    ap.add_argument("--log-dir", type=Path, help="where to save per-layer logs (default: a new temp dir)")
    args = ap.parse_args()

    selected = layers(args.workers)
    names = {layer.name for layer in selected}
    for opt in (args.only, args.skip):
        if opt and (bad := set(opt.split(",")) - names):
            ap.error(f"unknown layer(s) {sorted(bad)}; choose from {sorted(names)}")
    if args.only:
        selected = [layer for layer in selected if layer.name in args.only.split(",")]
    if args.skip:
        selected = [layer for layer in selected if layer.name not in args.skip.split(",")]

    return run_layers(selected, quiet=args.quiet, log_dir=args.log_dir,
                      title=f"test-fast (pytest workers: {args.workers})")


if __name__ == "__main__":
    sys.exit(main())
