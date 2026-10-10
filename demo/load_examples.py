"""Load every example agent in demo/examples/ with its seed files, suggested prompts and eval cases.

    uv run python demo/load_examples.py [--base-url http://localhost:8000] [--only SLUG] [--promote]

Idempotent, like demo/load.py: an agent that does not exist is created; one that exists gets a new
version only when its system prompt, tools, network policy or seed files changed (use --promote to make
that version prod); suggested prompts are replaced when they differ; eval cases are matched by name
(unchanged cases are kept, changed ones are replaced).

Each example is demo/examples/<slug>/agent.json plus seed/ and evals.json next to it. An agent.json
may instead point elsewhere: "extends" (a base agent definition), "seed_dir" and "evals", all relative
to the agent.json file. repo-maintainer uses this to reuse the reference demo unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"
sys.path.insert(0, str(HERE))
from load import CASE_FIELDS, check, read_seed_files  # noqa: E402

VERSION_FIELDS = ("system_prompt", "tools", "network", "seed_files")
LOCAL_KEYS = {"extends", "seed_dir", "evals"}


def resolve(example: Path) -> dict:
    """The full agent spec, its seed directory and its eval cases for one example directory."""
    raw = json.loads((example / "agent.json").read_text())
    spec: dict = {}
    if "extends" in raw:
        spec.update(json.loads((example / raw["extends"]).resolve().read_text()))
    spec.update({k: v for k, v in raw.items() if k not in LOCAL_KEYS})
    seed_dir = (example / raw.get("seed_dir", "seed")).resolve()
    evals_path = (example / raw.get("evals", "evals.json")).resolve()
    return {
        "dir": example,
        "spec": spec,
        "seed_dir": seed_dir,
        "evals_path": evals_path,
        "own_evals": "evals" not in raw,
        "cases": json.loads(evals_path.read_text()) if evals_path.exists() else [],
    }


def discover(only: list[str] | None = None) -> list[dict]:
    out = []
    for path in sorted(EXAMPLES.glob("*/agent.json")):
        ex = resolve(path.parent)
        if only is None or ex["spec"]["slug"] in only:
            out.append(ex)
    if only and len(out) != len(set(only)):
        have = sorted(p.parent.name for p in EXAMPLES.glob("*/agent.json"))
        sys.exit(f"unknown example slug in {only}; have {have}")
    return out


def load_one(api: httpx.Client, ex: dict, *, promote: bool) -> dict:
    spec = {**ex["spec"], "seed_files": read_seed_files(ex["seed_dir"])}
    slug = spec["slug"]
    prompts = spec.get("suggested_prompts", [])
    notes = []
    existing = api.get(f"/agents/{slug}")
    if existing.status_code == 404:
        agent = check(api.post("/agents", json=spec))
        notes.append(f"created (v{agent['prod_version']}, {len(spec['seed_files'])} seed files, "
                     f"API key {agent['api_key']} shown once)")
    else:
        agent = check(existing)
        prod = check(api.get(f"/agents/{slug}/versions/prod"))
        body = {k: spec[k] for k in VERSION_FIELDS if k in spec}
        if all(prod.get(k) == v for k, v in body.items()):
            notes.append(f"exists, prod v{prod['n']} already matches")
        else:
            body["rationale"] = "demo/load_examples.py: reload the example definition and seed files"
            version = check(api.post(f"/agents/{slug}/versions", json=body))
            notes.append(f"exists, created v{version['n']}")
            if promote:
                check(api.post(f"/agents/{slug}/promote", json={"version": version["n"]}))
                notes.append(f"promoted v{version['n']}")
        if agent.get("suggested_prompts") != prompts:
            agent = check(api.patch(f"/agents/{slug}", json={"suggested_prompts": prompts}))
            notes.append(f"{len(prompts)} suggested prompts updated")
        else:
            notes.append("suggested prompts unchanged")

    current = {c["name"]: c for c in check(api.get(f"/agents/{slug}/evals"))}
    added = kept = replaced = 0
    for case in ex["cases"]:
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
    notes.append(f"eval cases: {added} added, {replaced} replaced, {kept} unchanged")
    return {"slug": slug, "agent": agent, "notes": notes, "prompts": len(prompts), "cases": len(ex["cases"])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--only", action="append", help="only this example slug (repeatable)")
    ap.add_argument("--promote", action="store_true", help="promote new versions of existing agents")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base + "/v1", timeout=120) as api:
        results = [load_one(api, ex, promote=args.promote) for ex in discover(args.only)]
    for r in results:
        print(f"{r['slug']}: " + "; ".join(r["notes"]))
        print(f"  {r['prompts']} suggested prompts, {r['cases']} eval cases. "
              f"Open {base}/agents/{r['slug']} or share {base}/s/{r['slug']}?share={r['agent']['share_token']}")


if __name__ == "__main__":
    main()
