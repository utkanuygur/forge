"""Shared pieces for the example agents' build scripts (demo/examples/<slug>/build.py).

Each build script generates its seed files deterministically, then writes evals.json and
prompt_checks.json. Checkers are self contained bash that runs with cwd /workspace after the
agent finishes (exit 0 means pass); ground truth is embedded in the checker, never in the
workspace, so the agent cannot read or edit it. Each failing checker prints a `FAIL: ...` line.
"""

from __future__ import annotations

import json
from pathlib import Path

# Python helpers available to every checker body.
PY_HELPERS = r'''
import json, os, re, sys
from datetime import datetime, timezone

def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)

def check(cond, msg):
    if not cond:
        fail(msg)

def load_json(path):
    check(os.path.isfile(path), f"{path} was not written")
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError as exc:
        fail(f"{path} is not valid JSON: {exc}")

def field(obj, key, kind=None):
    check(isinstance(obj, dict), f"expected a JSON object, got {type(obj).__name__}")
    check(key in obj, f"missing field {key!r} (have {sorted(obj)})")
    value = obj[key]
    if kind is not None:
        ok = isinstance(value, kind) and not (kind in (int, float, (int, float)) and isinstance(value, bool))
        check(ok, f"field {key!r} has the wrong type: {value!r}")
    return value

def parse_time(value, what):
    check(isinstance(value, str), f"{what} should be an ISO 8601 string, got {value!r}")
    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        fail(f"{what} is not ISO 8601: {value!r}")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t

def near_time(value, expected, tolerance_s, what):
    got = parse_time(value, what)
    want = parse_time(expected, what)
    delta = abs((got - want).total_seconds())
    check(delta <= tolerance_s, f"{what} is {value}, expected about {expected} (within {tolerance_s}s)")

def near(value, expected, tolerance, what):
    check(isinstance(value, (int, float)) and not isinstance(value, bool), f"{what} should be a number: {value!r}")
    check(abs(value - expected) <= tolerance, f"{what} is {value}, expected {expected} (within {tolerance})")

def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()
'''


def py_checker(body: str, truth: object | None = None) -> str:
    """A bash checker that runs `body` (Python) with TRUTH and the helpers defined."""
    head = ""
    if truth is not None:
        head = f"TRUTH = json.loads({json.dumps(json.dumps(truth, sort_keys=True))})\n"
    script = PY_HELPERS.lstrip("\n") + head + body.strip("\n") + "\nprint(\"PASS\")\n"
    assert "FORGE_PY" not in script
    return f"cd /workspace 2>/dev/null || true\npython3 - <<'FORGE_PY'\n{script}FORGE_PY\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="\n") as f:
        f.write(text)


def write_json(path: Path, obj: object) -> None:
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def case(name: str, split: str, prompt: str, checker: str, checker_type: str = "shell") -> dict:
    assert split in ("train", "holdout") and checker_type in ("shell", "judge")
    return {"name": name, "split": split, "input": prompt.strip(), "seed_files": {},
            "checker_type": checker_type, "checker": checker.strip() + "\n" if checker_type == "shell" else checker.strip()}
