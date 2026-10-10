"""Rehearse the improvement demo with a real model and report whether it shows real improvement.

Usage: uv run python scripts/rehearse.py [--base-url http://localhost:8000] [--model anthropic:...]

Steps: load the demo agent, evaluate v1, run one improvement cycle, print per-split scores, the learned
lessons and a verdict. The demo is credible only if the candidate improves on HELD-OUT cases with zero
regressions; anything else prints a warning describing how to tune the demo.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import httpx


def poll(fn, done, timeout: float, every: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if done(value):
            return value
        time.sleep(every)
    raise TimeoutError("timed out waiting for the server")


def score(s: dict) -> str:
    return f"{s['passed']}/{s['total']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--model", help="override the agent's model (creates a new version)")
    args = ap.parse_args()
    api = httpx.Client(base_url=args.base_url.rstrip("/") + "/v1", timeout=60)

    subprocess.run([sys.executable, "demo/load.py", "--base-url", args.base_url, "--promote"], check=True)
    if args.model:
        v = api.post("/agents/repo-maintainer/versions", json={"model": args.model, "lessons": [],
                     "rationale": "rehearsal model override"}).raise_for_status().json()
        api.post("/agents/repo-maintainer/promote", json={"version": v["n"]}).raise_for_status()

    agent = api.get("/agents/repo-maintainer").raise_for_status().json()
    prod = agent["prod_version"]
    print(f"\nEvaluating v{prod} ...")
    api.post("/agents/repo-maintainer/evals/run", json={"version": prod}).raise_for_status()
    base = poll(lambda: api.get(f"/agents/repo-maintainer/evals/results?version={prod}").json(),
                lambda r: not r.get("running"), timeout=1800)
    print(f"  v{prod}: train {score(base['train'])}, holdout {score(base['holdout'])}")
    for r in base["results"]:
        print(f"    {'PASS' if r['passed'] else 'FAIL'}  {r['case_id']}  {r['reason'][:100]!r}")

    print("\nImproving ...")
    im = api.post("/agents/repo-maintainer/improve", json={}).raise_for_status().json()
    im = poll(lambda: api.get(f"/improvements/{im['id']}").json(), lambda x: x["status"] != "running", timeout=2400)
    print(f"  status: {im['status']}" + (f" ({im['error']})" if im.get("error") else ""))
    if not im.get("candidate"):
        print("\nWARN: no candidate was produced; see the error above.")
        return 1
    print(f"  v{im['base_version']}: train {score(im['base']['train'])}, holdout {score(im['base']['holdout'])}")
    print(f"  v{im['candidate_version']}: train {score(im['candidate']['train'])}, holdout {score(im['candidate']['holdout'])}")
    print("  lessons:")
    for e in im["lessons_diff"]:
        print(f"    [{e['op']}] {e['text']}")

    b, c = im["base"]["holdout"]["passed"], im["candidate"]["holdout"]["passed"]
    print()
    if base["train"]["passed"] + base["holdout"]["passed"] == base["train"]["total"] + base["holdout"]["total"]:
        print("WARN: v1 already passes everything; make the conventions less guessable.")
    elif im["regressions"]:
        print(f"WARN: regressions {im['regressions']}; the promotion rule blocked this candidate.")
    elif c <= b:
        print("WARN: no held-out improvement; lessons may be too case-specific. Inspect the lessons above.")
    else:
        print(f"OK: held-out improved {b} -> {c} with zero regressions. The demo is credible.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
