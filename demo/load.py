"""Load the Forge reference demo: the repo-maintainer agent and its eval cases.

    uv run python demo/load.py [--base-url http://localhost:8000] [--promote] [--rotate-key]

Seed files come from demo/acme-orders. If the agent already exists, a new version is
created with the current system prompt and seed files (use --promote to make it prod).
Eval cases are matched by name: unchanged cases are kept, changed ones are replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
SEED_DIR = HERE / "acme-orders"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git", ".mypy_cache"}
SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
CASE_FIELDS = ("name", "input", "seed_files", "checker_type", "checker", "split")


def read_seed_files(root: Path = SEED_DIR) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
            continue
        files[rel.as_posix()] = path.read_text()
    return files


def check(response: httpx.Response) -> dict | list:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url} -> "
                 f"{response.status_code}: {response.text}")
    return response.json()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--promote", action="store_true", help="promote the new version if the agent existed")
    ap.add_argument("--rotate-key", action="store_true", help="issue a new API key if the agent existed")
    args = ap.parse_args()

    spec = json.loads((HERE / "repo-maintainer.json").read_text())
    spec["seed_files"] = read_seed_files()
    cases = json.loads((HERE / "evals.json").read_text())
    slug = spec["slug"]

    with httpx.Client(base_url=args.base_url.rstrip("/") + "/v1", timeout=60) as api:
        existing = api.get(f"/agents/{slug}")
        api_key = None
        if existing.status_code == 404:
            agent = check(api.post("/agents", json=spec))
            api_key = agent["api_key"]
            print(f"created agent {slug} (v{agent['prod_version']}, "
                  f"{len(spec['seed_files'])} seed files)")
        else:
            check(existing)
            body = {k: spec[k] for k in ("system_prompt", "tools", "network", "seed_files")}
            prod = check(api.get(f"/agents/{slug}/versions/prod"))
            if all(prod.get(k) == v for k, v in body.items()):
                version = prod
                print(f"agent {slug} exists: prod v{prod['n']} already matches, no new version")
            else:
                body["rationale"] = "demo/load.py: reload system prompt and seed repo"
                version = check(api.post(f"/agents/{slug}/versions", json=body))
                print(f"agent {slug} exists: created version v{version['n']}")
            if args.promote and version["n"] != prod["n"]:
                check(api.post(f"/agents/{slug}/promote", json={"version": version["n"]}))
                print(f"promoted v{version['n']} to prod")
            if args.rotate_key:
                api_key = check(api.post(f"/agents/{slug}/rotate-key"))["api_key"]
            agent = check(api.get(f"/agents/{slug}"))

        current = {c["name"]: c for c in check(api.get(f"/agents/{slug}/evals"))}
        added = kept = replaced = 0
        for case in cases:
            body = {k: case[k] for k in CASE_FIELDS}
            old = current.get(case["name"])
            if old and all(old.get(k) == body[k] for k in CASE_FIELDS):
                kept += 1
                continue
            if old:
                check(api.delete(f"/evals/{old['id']}"))
                replaced += 1
            else:
                added += 1
            check(api.post(f"/agents/{slug}/evals", json=body))
        print(f"eval cases: {added} added, {replaced} replaced, {kept} unchanged")

    print()
    if api_key:
        print(f"API key:     {api_key}   (shown once, save it)")
    else:
        print("API key:     unchanged (pass --rotate-key to issue a new one)")
    print(f"Share token: {agent['share_token']}")
    base = args.base_url.rstrip("/")
    print(f"Try it:      curl -X POST '{base}/v1/agents/{slug}/runs?share={agent['share_token']}' \\\n"
          f"               -H 'content-type: application/json' "
          f"-d '{{\"input\": \"Add a GET /orders/{{order_id}}/receipt endpoint\"}}'")


if __name__ == "__main__":
    main()
