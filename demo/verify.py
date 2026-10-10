"""Check that every eval case separates good work from plausible but wrong work.

For each case in demo/evals.json, on a fresh copy of demo/acme-orders (git initialized):
  1. the untouched seed repo          -> checker must FAIL
  2. demo/solutions/<case>/naive.py    -> checker must FAIL (works, but breaks a convention)
  3. demo/solutions/<case>/reference.py -> checker must PASS

Checkers run in the forge-sandbox image with no network when it exists, otherwise locally
through `uv run --with fastapi --with httpx --with pytest --with ruff`.

    uv run python demo/verify.py [--runner auto|docker|local] [--case NAME] [-v]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "acme-orders"
SOLUTIONS = HERE / "solutions"
IMAGE = "forge-sandbox"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".venv")
LOCAL_DEPS = ["fastapi", "httpx", "pytest", "ruff", "pydantic", "uvicorn"]
EXPECT = {"seed": False, "naive": False, "reference": True}


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def prepare(case: dict, variant: str, root: Path) -> Path:
    ws = root / f"{case['name']}-{variant}"
    shutil.copytree(SEED, ws, ignore=IGNORE)
    for rel, content in case.get("seed_files", {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content)
    git = ["git", "-c", "user.name=forge", "-c", "user.email=forge@localhost"]
    subprocess.run(git + ["init", "-q"], cwd=ws, check=True)
    subprocess.run(git + ["add", "-A"], cwd=ws, check=True)
    subprocess.run(git + ["commit", "-qm", "baseline"], cwd=ws, check=True)
    if variant != "seed":
        script = SOLUTIONS / case["name"] / f"{variant}.py"
        subprocess.run(
            [sys.executable, str(script), str(ws)],
            check=True,
            env={"PYTHONPATH": str(SOLUTIONS), "PATH": "/usr/bin:/bin"},
        )
    return ws


def run_checker(ws: Path, checker: str, runner: str) -> tuple[bool, str, float]:
    if runner == "docker":
        # Run as the owner of the mounted workspace: on Linux the bind mount keeps host ownership, so the
        # image's default `agent` user could not write caches (Docker Desktop on macOS hides this).
        cmd = ["docker", "run", "--rm", "--network", "none", "-v", f"{ws}:/workspace",
               "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               "-w", "/workspace", IMAGE, "bash", "-lc", checker]
    else:
        cmd = ["uv", "run", "-q", "--no-project", "--python", "3.12"]
        for dep in LOCAL_DEPS:
            cmd += ["--with", dep]
        cmd += ["bash", "-c", checker]
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=300)
        out, ok = r.stdout + r.stderr, r.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "TIMEOUT", False
    return ok, out, time.monotonic() - start


def reason(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    fails = [line for line in lines if "FAIL" in line]
    return (fails or lines or [""])[-1][:110]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", choices=["auto", "docker", "local"], default="auto")
    ap.add_argument("--case", action="append", help="only these case names")
    ap.add_argument("-v", "--verbose", action="store_true", help="print full checker output")
    args = ap.parse_args()

    runner = args.runner
    if runner == "auto":
        runner = "docker" if docker_available() else "local"
    if runner == "local":
        print(f"note: image {IMAGE!r} not available, running checkers locally via uv")
    print(f"runner: {runner}\n")

    cases = json.loads((HERE / "evals.json").read_text())
    if args.case:
        cases = [c for c in cases if c["name"] in args.case]

    jobs = [(c, v) for c in cases for v in EXPECT]
    with tempfile.TemporaryDirectory(prefix="forge-verify-") as tmp:
        root = Path(tmp)

        def job(item):
            case, variant = item
            ws = prepare(case, variant, root)
            return case, variant, *run_checker(ws, case["checker"], runner)

        with ThreadPoolExecutor(max_workers=4 if runner == "docker" else 6) as pool:
            results = list(pool.map(job, jobs))

    failures = 0
    print(f"{'case':26} {'split':8} {'variant':10} {'result':6} {'ok':4} {'secs':>5}  reason")
    for case, variant, passed, out, secs in results:
        good = passed == EXPECT[variant]
        failures += not good
        print(
            f"{case['name']:26} {case['split']:8} {variant:10} {'PASS' if passed else 'FAIL':6} "
            f"{'yes' if good else 'NO':4} {secs:5.1f}  {reason(out)}"
        )
        if args.verbose or not good:
            print("    " + out.strip().replace("\n", "\n    "))
    print(f"\n{len(results) - failures}/{len(results)} expectations hold")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
