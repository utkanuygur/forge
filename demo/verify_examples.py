"""Check that every example agent's shell checkers separate correct work from no work.

For each example in demo/examples/<slug>/ and each shell checker (eval cases in evals.json and the
suggested prompt checks in prompt_checks.json), on a fresh git initialized copy of seed/:
  1. the untouched seed                          -> checker must FAIL
  2. a plausible but wrong answer, when present  -> checker must FAIL  (solutions/<name>.naive[.py])
  3. the reference solution in solutions/        -> checker must PASS  (solutions/<name>[.py])
     (a .py is run as `python <script> <workspace>`, a directory is copied over the workspace;
      suggested prompts use solutions/prompt-<title slug>)

Judge cases are listed as "judge, not shell-verified": their rubrics are exercised with the fake grader in
the unit tests. With --judge, they are also graded by the real grader model (FORGE_GRADER_MODEL, needs
ANTHROPIC_API_KEY): the seed must fail and the reference must pass. That costs a few cents.

Checkers run in the forge-sandbox image with no network:
  docker run --rm --network none --user $(id -u):$(id -g) -e HOME=/tmp -v <ws>:/workspace -w /workspace forge-sandbox

    uv run python demo/verify_examples.py [--example SLUG] [--case NAME] [--judge] [-v]

repo-maintainer's eval cases are verified by demo/verify.py (21 expectations), not here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"
IMAGE = os.environ.get("FORGE_SANDBOX_IMAGE", "forge-sandbox")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".venv", ".DS_Store")
EXPECT = {"seed": False, "naive": False, "reference": True}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def examples(only: list[str] | None = None) -> list[Path]:
    out = []
    for spec in sorted(EXAMPLES.glob("*/agent.json")):
        d = spec.parent
        if (d / "seed").is_dir() and (only is None or d.name in only):
            out.append(d)
    return out


def items(example: Path) -> list[dict]:
    """Every checker of an example: eval cases plus suggested prompt checks."""
    out = []
    for c in json.loads((example / "evals.json").read_text()):
        out.append({**c, "kind": "eval", "solution": c["name"]})
    checks_file = example / "prompt_checks.json"
    titles = [p["title"] for p in json.loads((example / "agent.json").read_text()).get("suggested_prompts", [])]
    if checks_file.exists():
        for title, checker in json.loads(checks_file.read_text()).items():
            if title not in titles:
                raise SystemExit(f"{example.name}: prompt_checks.json has {title!r}, not a suggested prompt")
            out.append({"name": f"prompt: {title}", "split": "-", "checker_type": "shell", "checker": checker,
                        "seed_files": {}, "kind": "prompt", "solution": "prompt-" + slugify(title)})
    return out


def git_baseline(ws: Path) -> None:
    git = ["git", "-c", "user.name=forge", "-c", "user.email=forge@localhost"]
    subprocess.run(git + ["init", "-q"], cwd=ws, check=True)
    subprocess.run(git + ["add", "-A"], cwd=ws, check=True)
    subprocess.run(git + ["commit", "-qm", "baseline"], cwd=ws, check=True)
    subprocess.run(git + ["tag", "baseline"], cwd=ws, check=True)


def has_solution(example: Path, name: str) -> bool:
    sol = example / "solutions"
    return (sol / name).is_dir() or (sol / f"{name}.py").is_file()


def solution_name(item: dict, variant: str) -> str:
    return item["solution"] + (".naive" if variant == "naive" else "")


def apply_solution(example: Path, name: str, ws: Path) -> None:
    sol = example / "solutions"
    if (sol / name).is_dir():
        shutil.copytree(sol / name, ws, dirs_exist_ok=True, ignore=IGNORE)
    elif (sol / f"{name}.py").is_file():
        subprocess.run([sys.executable, str(sol / f"{name}.py"), str(ws)], check=True,
                       env={"PYTHONPATH": str(sol), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    else:
        raise FileNotFoundError(f"no reference solution {sol / name}(.py)")


def prepare(example: Path, item: dict, variant: str, root: Path) -> Path:
    ws = root / f"{example.name}-{slugify(item['name'])}-{variant}"
    shutil.copytree(example / "seed", ws, ignore=IGNORE)
    for rel, content in item.get("seed_files", {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content)
    git_baseline(ws)
    if variant != "seed":
        apply_solution(example, solution_name(item, variant), ws)
    return ws


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def run_checker(ws: Path, checker: str) -> tuple[bool, str, float]:
    cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
           "-v", f"{ws}:/workspace", "-w", "/workspace", IMAGE, "bash", "-lc", checker]
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        out, ok = r.stdout + r.stderr, r.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "TIMEOUT", False
    return ok, out, time.monotonic() - start


def reason(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    fails = [line for line in lines if "FAIL" in line]
    return (fails or lines or [""])[-1][:100]


def judge_variant(example: Path, item: dict, variant: str, root: Path) -> tuple[bool, str, float]:
    from forge.improve import judge
    from forge.providers import make_provider
    from forge.server.evals import grader_model

    ws = prepare(example, item, variant, root)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "baseline"], cwd=ws, capture_output=True, text=True).stdout
    output = "Done. The requested files are written in the workspace." if variant == "reference" else "Done."
    start = time.monotonic()
    passed, why = asyncio.run(judge(item["checker"], item["input"], output, diff, make_provider(grader_model())))
    return passed, why, time.monotonic() - start


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--example", action="append", help="only these example slugs")
    ap.add_argument("--case", action="append", help="only items whose name contains this text")
    ap.add_argument("--judge", action="store_true", help="also grade judge cases with the real grader model")
    ap.add_argument("-v", "--verbose", action="store_true", help="print full checker output")
    args = ap.parse_args()
    if not docker_available():
        print(f"image {IMAGE!r} not found: build it with `make image`")
        return 2

    shell_jobs, judge_items = [], []
    for ex in examples(args.example):
        for it in items(ex):
            if args.case and not any(c in it["name"] for c in args.case):
                continue
            if it["checker_type"] == "shell":
                shell_jobs += [(ex, it, v) for v in EXPECT
                               if v != "naive" or has_solution(ex, solution_name(it, v))]
            else:
                judge_items.append((ex, it))

    with tempfile.TemporaryDirectory(prefix="forge-verify-examples-") as tmp:
        root = Path(tmp)

        def job(j):
            ex, it, variant = j
            ws = prepare(ex, it, variant, root)
            return ex, it, variant, *run_checker(ws, it["checker"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(job, shell_jobs))

        judged = []
        if args.judge:
            from dotenv import load_dotenv

            load_dotenv(HERE.parent / ".env")
            for ex, it in judge_items:
                for variant in ("seed", "reference"):
                    judged.append((ex, it, variant, *judge_variant(ex, it, variant, root)))

    failures = 0
    print(f"{'example':15} {'case':42} {'split':8} {'variant':10} {'result':6} {'ok':4} {'secs':>5}  reason")
    for ex, it, variant, passed, out, secs in results + judged:
        good = passed == EXPECT[variant]
        failures += not good
        label = it["name"] if it["checker_type"] == "shell" else f"{it['name']} (judge)"
        print(f"{ex.name:15} {label[:42]:42} {it['split']:8} {variant:10} {'PASS' if passed else 'FAIL':6} "
              f"{'yes' if good else 'NO':4} {secs:5.1f}  {reason(out) if it['checker_type'] == 'shell' else out[:100]}")
        if args.verbose or not good:
            print("    " + out.strip().replace("\n", "\n    "))
    if not args.judge:
        for ex, it in judge_items:
            print(f"{ex.name:15} {it['name'][:42]:42} {it['split']:8} {'-':10} {'-':6} {'-':4} {'-':>5}  "
                  "judge, not shell-verified (fake grader in tests/test_examples.py; --judge for the real grader)")
    total = len(results) + len(judged)
    print(f"\n{total - failures}/{total} expectations hold"
          + (f"; {len(judge_items)} judge cases not shell-verified" if not args.judge and judge_items else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
