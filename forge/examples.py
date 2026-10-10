"""Example agents shipped with Forge: discovery, staging for the desktop bundle, and installation.

Each example is demo/examples/<key>/agent.json plus seed/ and evals.json next to it. An agent.json may instead
point elsewhere: "extends" (a base agent definition), "seed_dir" and "evals", all relative to the agent.json
file. repo-maintainer uses this to reuse the reference demo (demo/repo-maintainer.json, demo/acme-orders and
demo/evals.json) unchanged.

The demo directory is found the way forge/db.py finds schema.sql: next to the package in a checkout, and inside
the PyInstaller bundle (sys._MEIPASS, the `_internal` directory) when frozen. FORGE_EXAMPLES_DIR overrides it.
Only what an install needs is read or bundled: agent definitions, seed files and evals.json. Reference
solutions/, build scripts and prompt_checks.json are never read or shipped.

`python -m forge.examples stage DEST` copies exactly those files into DEST (laid out like demo/), which is what
desktop/scripts/build-server.sh bundles.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from forge import db

CASE_FIELDS = ("name", "input", "seed_files", "checker_type", "checker", "split")
VERSION_FIELDS = ("system_prompt", "tools", "http_tools", "network", "seed_files", "model")
LOCAL_KEYS = {"extends", "seed_dir", "evals"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git", ".mypy_cache", "solutions"}
SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


class ExampleError(ValueError):
    """An unknown example key or a broken example definition."""


# ---------------------------------------------------------------- locating and reading


def demo_dir() -> Path:
    """The directory that holds examples/ (and the files examples refer to, like acme-orders/)."""
    if override := os.environ.get("FORGE_EXAMPLES_DIR"):
        return Path(override).resolve()
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "demo"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1] / "demo"


def examples_dir() -> Path:
    return demo_dir() / "examples"


def read_seed_files(root: Path) -> dict[str, str]:
    """Text seed files under root keyed by relative POSIX path, skipping caches and reference solutions."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
            continue
        files[rel.as_posix()] = path.read_text()
    return files


def resolve(example: Path) -> dict[str, Any]:
    """The full agent spec, its seed directory and its eval cases for one example directory."""
    agent_json = example / "agent.json"
    raw = json.loads(agent_json.read_text())
    spec: dict[str, Any] = {}
    if "extends" in raw:
        spec.update(json.loads((example / raw["extends"]).resolve().read_text()))
    spec.update({k: v for k, v in raw.items() if k not in LOCAL_KEYS})
    seed_dir = (example / raw.get("seed_dir", "seed")).resolve()
    evals_path = (example / raw.get("evals", "evals.json")).resolve()
    return {
        "key": example.name,
        "dir": example,
        "spec": spec,
        "seed_dir": seed_dir,
        "evals_path": evals_path,
        "base_path": (example / raw["extends"]).resolve() if "extends" in raw else None,
        "own_evals": "evals" not in raw,
        "cases": json.loads(evals_path.read_text()) if evals_path.exists() else [],
    }


def discover(only: list[str] | None = None, root: Path | None = None) -> list[dict[str, Any]]:
    """Every example under root (default examples_dir()), sorted by key; `only` filters by slug or key."""
    root = root or examples_dir()
    found = [resolve(p.parent) for p in sorted(root.glob("*/agent.json"))] if root.is_dir() else []
    if only is None:
        return found
    out = [ex for ex in found if ex["spec"]["slug"] in only or ex["key"] in only]
    if len(out) != len(set(only)):
        raise ExampleError(f"unknown example in {sorted(set(only))}; have {[ex['key'] for ex in found]}")
    return out


def get(key: str, root: Path | None = None) -> dict[str, Any]:
    for ex in discover(root=root):
        if key in (ex["key"], ex["spec"]["slug"]):
            return ex
    raise ExampleError(f"example {key!r} not found")


def needs_network(spec: dict[str, Any]) -> bool:
    return bool(spec.get("network")) or bool(spec.get("http_tools"))


def summary(ex: dict[str, Any]) -> dict[str, Any]:
    """The GET /v1/examples item for one example."""
    spec = ex["spec"]
    return {
        "key": ex["key"], "name": spec.get("name") or spec["slug"], "description": spec.get("description", ""),
        "agent_slug": spec["slug"], "installed": db.get_agent_by_slug(spec["slug"]) is not None,
        "eval_cases": len(ex["cases"]), "suggested_prompts": len(spec.get("suggested_prompts") or []),
        "needs_network": needs_network(spec),
    }


# ---------------------------------------------------------------- staging for the desktop bundle


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def stage(dest: Path, src: Path | None = None) -> list[str]:
    """Copies only what installs need from src (default demo_dir()) into dest, keeping the relative layout so
    "extends", "seed_dir" and "evals" still resolve. Returns the copied paths relative to dest."""
    src = (src or demo_dir()).resolve()
    dest = Path(dest)
    copied: set[Path] = set()

    def take(path: Path) -> None:
        rel = path.resolve().relative_to(src)  # an example pointing outside demo/ is a packaging error
        if rel not in copied:
            _copy(path, dest / rel)
            copied.add(rel)

    for ex in discover(root=src / "examples"):
        take(ex["dir"] / "agent.json")
        if ex["base_path"]:
            take(ex["base_path"])
        if ex["evals_path"].exists():
            take(ex["evals_path"])
        for name in read_seed_files(ex["seed_dir"]):
            take(ex["seed_dir"] / name)
    return sorted(p.as_posix() for p in copied)


# ---------------------------------------------------------------- installing into the database


def _case_body(case: dict[str, Any]) -> dict[str, Any]:
    return {k: case.get(k) if k != "seed_files" else case.get(k) or {} for k in CASE_FIELDS}


def sync_cases(agent_id: str, cases: list[dict[str, Any]]) -> dict[str, int]:
    """Cases are matched by name: unchanged ones are kept, changed ones replaced, missing ones added."""
    current = {c["name"]: c for c in db.list_eval_cases(agent_id)}
    added = kept = replaced = 0
    for case in cases:
        body = _case_body(case)
        old = current.get(body["name"])
        if old and all(old.get(k) == body[k] for k in CASE_FIELDS):
            kept += 1
            continue
        if old:
            db.delete_eval_case(old["id"])
            replaced += 1
        else:
            added += 1
        db.create_eval_case(agent_id, body["name"], body["input"], body["checker_type"], body["checker"],
                            body["split"], body["seed_files"])
    return {"added": added, "replaced": replaced, "kept": kept}


def install(ex: dict[str, Any], *, default_model: str, model: str | None = None, replace: bool = False,
            promote: bool = False) -> dict[str, Any]:
    """Creates the example agent with its seed files, eval cases and suggested prompts.

    The model is `model` when given, else the example's own model, else default_model (on replace: else the
    current prod model, so a reinstall does not switch models by itself). When the slug exists: raises FileExistsError unless replace, in which case a new version is created only if
    the definition, seed files or model differ (promoted when promote), the suggested prompts are restored, and
    eval cases are synced by name. Returns {"agent", "api_key" (None unless created), "created", "version",
    "cases"}.
    """
    spec = {**ex["spec"], "seed_files": read_seed_files(ex["seed_dir"])}
    slug = spec["slug"]
    prompts = spec.get("suggested_prompts") or []
    existing = db.get_agent_by_slug(slug)
    prod = db.get_version(existing["prod_version_id"]) if existing else None
    spec["model"] = model or spec.get("model") or (prod["model"] if prod else None) or default_model
    fields = {k: spec[k] for k in VERSION_FIELDS if k in spec}
    if existing is None:
        agent, key = db.create_agent(slug, spec.get("name") or slug, spec.get("description", ""),
                                     bool(spec.get("auto_promote", False)), prompts)
        v = db.create_version(agent["id"], **fields, lessons=spec.get("lessons"),
                              rationale=f"Installed from the {ex['key']} example")
        cases = sync_cases(agent["id"], ex["cases"])
        agent = db.set_prod_version(agent["id"], v["id"], "create")
        return {"agent": agent, "api_key": key, "created": True, "version": v["n"], "cases": cases}
    if not replace:
        raise FileExistsError(f"An agent with the slug {slug!r} already exists. Open it, or reinstall the example "
                              "with ?replace=1 to add a new version from the example definition.")
    agent = existing
    version_n = prod["n"] if prod else None
    if prod is None or any(prod.get(k) != v for k, v in fields.items()):
        base = {k: prod[k] for k in ("lessons", "http_tools")} if prod else {}
        v = db.create_version(agent["id"], **{**base, **fields}, parent_id=prod["id"] if prod else None,
                              created_by="human", rationale=f"Reinstalled from the {ex['key']} example")
        version_n = v["n"]
        if promote or prod is None:
            agent = db.set_prod_version(agent["id"], v["id"], "human")  # type: ignore[assignment]
    if agent.get("suggested_prompts") != prompts:
        agent = db.update_agent(agent["id"], suggested_prompts=prompts)  # type: ignore[assignment]
    cases = sync_cases(agent["id"], ex["cases"])
    return {"agent": db.get_agent(agent["id"]), "api_key": None, "created": False, "version": version_n,
            "cases": cases}


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m forge.examples")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stage", help="copy the example data an install needs into DEST (for the desktop bundle)")
    st.add_argument("dest", type=Path)
    sub.add_parser("list", help="list the examples found")
    args = ap.parse_args(argv)
    if args.cmd == "stage":
        if args.dest.exists():
            shutil.rmtree(args.dest)
        files = stage(args.dest)
        print(f"staged {len(files)} files from {demo_dir()} into {args.dest}")
    else:
        for ex in discover():
            print(f"{ex['key']}: {ex['spec'].get('name')} ({len(ex['cases'])} eval cases)")


if __name__ == "__main__":
    main()
