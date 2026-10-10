#!/usr/bin/env python3
"""Run only the tests affected by the changes on this branch (for iterating; the full suite stays the gate).

    uv run python scripts/test_changed.py                 # changes since master: committed, staged, unstaged, untracked
    uv run python scripts/test_changed.py --base main --dry-run
    uv run python scripts/test_changed.py --files forge/pruning.py web/src/pages/Runs.tsx --dry-run

Selection is deterministic and static, so it also covers code that runs in the e2e server subprocess
(a coverage tool such as pytest-testmon cannot see that without extra work):

  Python unit tests  An import graph built with `ast` over forge/, tests/ and demo/: a test module is
                     selected when it imports a changed module directly or through other forge or test
                     modules. Changed tests, conftest.py and helpers select themselves and their importers
                     (a conftest selects its whole directory). Tests that read demo/ files by path are
                     selected when demo/ changes.
  Python e2e tests   The server runs nearly all of forge/, so a change to a core module (CORE_E2E below,
                     including everything under forge/server/ and schema.sql) selects every e2e module. A
                     few feature modules map to the e2e modules that exercise them (FEATURE_E2E); any other
                     forge module the server or CLI imports also selects every e2e module.
  Web unit (Vitest)  `vitest related <changed web files> --run`.
  Browser (Playwright) Changed specs run alone; a change to web/src, the web build config, or a backend
                     module the UI flows depend on runs every spec (feature modules map to their specs).
  Demo checks        demo/verify.py and demo/verify_examples.py when demo/, sandbox/, forge/executors.py or
                     forge/loop.py change.
  Everything         pyproject.toml or uv.lock (dependencies) select the whole Python suite; web/package*.json
                     the whole web suite. Markdown and docs select nothing.

The selection and the reason for each item are printed first, then the layers run in parallel through
scripts/test_fast.py. Use `make test` (or `make test-fast`) once before merging.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fast import REPO, Layer, default_workers, run_layers  # noqa: E402

# forge modules every server request path goes through: a change here can break any e2e test or browser flow.
CORE_E2E = {"forge.server", "forge.loop", "forge.executors", "forge.db", "forge.types", "forge.providers",
            "forge.improve", "forge.schema"}
# Feature modules exercised end to end only by these e2e modules and browser specs.
FEATURE_E2E = {
    "forge.drafting": (["test_authoring"], ["authoring"]),
    "forge.pruning": (["test_authoring"], ["authoring"]),
    "forge.provenance": (["test_authoring", "test_improvement", "test_observability", "test_repeats"],
                         ["authoring", "configure", "improve"]),
    "forge.metrics": (["test_observability", "test_authoring"], ["observability", "runs"]),
    "forge.pricing": (["test_observability", "test_authoring", "test_improvement"], ["observability", "runs"]),
    "forge.cli": (["test_cli"], []),
}
DEMO_TRIGGERS = ("demo/", "sandbox/", "forge/executors.py", "forge/loop.py")
ALL_PYTHON = ("pyproject.toml", "uv.lock")
WEB_ALL = ("web/package.json", "web/package-lock.json", "web/vite.config.ts", "web/vitest.config.ts",
           "web/tsconfig.json", "web/playwright.config.ts", "web/tailwind.config.js", "web/postcss.config.js")
IGNORED_SUFFIXES = (".md",)
IGNORED_PREFIXES = ("docs/", ".github/", ".claude/")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return [line for line in out.stdout.splitlines() if line]


def changed_files(base: str) -> list[str]:
    files = set(git("diff", "--name-only", f"{base}...HEAD"))  # committed on this branch
    files |= set(git("diff", "--name-only", "HEAD"))  # staged and unstaged
    files |= set(git("ls-files", "--others", "--exclude-standard"))  # new, untracked
    return sorted(files)


# ---------------------------------------------------------------- import graph


def module_name(path: str) -> str | None:
    """forge/server/app.py -> forge.server.app; tests/e2e/conftest.py -> tests.e2e.conftest; schema.sql -> forge.schema."""
    p = Path(path)
    if p.parts[0] not in ("forge", "tests", "demo") or p.suffix not in (".py", ".sql"):
        return None
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path, mod: str) -> set[str]:
    """Absolute module names imported anywhere in the file (also inside functions), relative ones resolved."""
    try:
        tree = ast.parse(path.read_text(), str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    pkg = mod.split(".")[:-1] if path.name != "__init__.py" else mod.split(".")
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(pkg[: len(pkg) - node.level + 1] + ([base] if base else []))
            elif path.parts[-2:-1] == ("demo",) and base and (REPO / "demo" / f"{base}.py").exists():
                base = f"demo.{base}"  # demo scripts put demo/ on sys.path
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}  # `from forge import db` imports forge.db
    if "load_examples" in path.read_text():  # tests load demo/load_examples.py by path
        found.add("demo.load_examples")
    return found


@dataclass
class Graph:
    modules: dict[str, Path] = field(default_factory=dict)
    importers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    @classmethod
    def build(cls) -> Graph:
        g = cls()
        for root in ("forge", "tests", "demo"):
            for path in sorted((REPO / root).rglob("*.py")):
                rel = path.relative_to(REPO)
                if "__pycache__" in rel.parts or (mod := module_name(str(rel))) is None:
                    continue
                g.modules[mod] = path
        g.modules["forge.schema"] = REPO / "forge" / "schema.sql"
        for mod, path in g.modules.items():
            if path.suffix != ".py":
                continue
            for imp in imports_of(path, mod):
                # Importing forge.server.app also imports the package forge.server; record the closest known module.
                while imp and imp not in g.modules:
                    imp = imp.rpartition(".")[0]
                if imp and imp != mod:
                    g.importers[imp].add(mod)
        g.importers["forge.schema"] |= {"forge.db"}  # db.py executes schema.sql
        return g

    def dependents(self, mod: str) -> set[str]:
        """mod plus every module that imports it, transitively."""
        seen, todo = {mod}, [mod]
        while todo:
            for m in self.importers.get(todo.pop(), ()):
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        return seen

    def closure(self, mod: str) -> set[str]:
        """mod plus everything it imports, transitively (within forge/tests/demo)."""
        imports: dict[str, set[str]] = defaultdict(set)
        for target, srcs in self.importers.items():
            for s in srcs:
                imports[s].add(target)
        seen, todo = {mod}, [mod]
        while todo:
            for m in imports.get(todo.pop(), ()):
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        return seen


# ---------------------------------------------------------------- selection


@dataclass
class Selection:
    python: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))  # test path -> reasons
    python_all: list[str] = field(default_factory=list)
    vitest: list[str] = field(default_factory=list)  # changed web files, relative to web/
    vitest_all: list[str] = field(default_factory=list)
    specs: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    specs_all: list[str] = field(default_factory=list)
    demo: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def add_py(self, test: str, why: str) -> None:
        if why not in self.python[test]:
            self.python[test].append(why)


def is_test_module(mod: str) -> bool:
    return mod.startswith("tests.") and mod.rsplit(".", 1)[-1].startswith("test_") and not mod.startswith("tests.live")


def test_path(mod: str) -> str:
    return mod.replace(".", "/") + ".py"


def e2e_modules() -> list[str]:
    return sorted(p.stem for p in (REPO / "tests" / "e2e").glob("test_*.py"))


def select(files: list[str], g: Graph) -> Selection:
    sel = Selection()
    e2e = e2e_modules()
    server_side = g.closure("forge.server.app") | g.closure("forge.cli")
    demo_readers = [m for m, p in g.modules.items()
                    if is_test_module(m) and p.suffix == ".py" and '"demo"' in p.read_text()]

    for f in files:
        hit = False
        if f.endswith(IGNORED_SUFFIXES) or f.startswith(IGNORED_PREFIXES):
            continue
        if f in ALL_PYTHON:
            sel.python_all.append(f"{f} (dependencies)")
            hit = True
        if f.startswith(DEMO_TRIGGERS):
            sel.demo.append(f)
            sel.examples.append(f)
            hit = True
        if f.startswith("sandbox/"):
            sel.add_py("tests", f"{f}: sandbox image (docker and e2e tests)")
        if f.startswith("demo/"):
            for m in demo_readers:
                sel.add_py(test_path(m), f"{f}: test reads demo/ files")

        mod = module_name(f)
        if mod and (mod in g.modules or (REPO / f).suffix == ".py"):
            hit = True
            if mod.startswith("tests.") and not mod.startswith("tests.live") and Path(f).name == "conftest.py":
                d = str(Path(f).parent)
                sel.add_py(d, f"{f}: conftest for {d}/")
            for dep in sorted(g.dependents(mod)):
                if is_test_module(dep):
                    sel.add_py(test_path(dep), f"{f}: changed test" if dep == mod else f"{f}: imported (maybe indirectly)")
                elif (dep != mod and dep.startswith("tests.") and not dep.startswith("tests.live")
                      and Path(g.modules[dep]).name == "conftest.py"):
                    d = str(Path(test_path(dep)).parent)
                    sel.add_py(d, f"{f}: used by {d}/conftest.py")
            if mod.startswith("forge."):
                key = next((k for k in sorted(CORE_E2E, key=len, reverse=True) if mod == k or mod.startswith(k + ".")), None)
                if key:
                    sel.add_py("tests/e2e", f"{f}: core server module, every e2e test")
                    sel.specs_all.append(f"{f}: core server module")
                elif mod in FEATURE_E2E:
                    tests, specs = FEATURE_E2E[mod]
                    for t in tests:
                        if t in e2e:
                            sel.add_py(f"tests/e2e/{t}.py", f"{f}: feature exercised end to end")
                    for s in specs:
                        sel.specs[f"tests/e2e/{s}.spec.ts"].append(f"{f}: feature UI flow")
                elif mod in server_side:
                    sel.add_py("tests/e2e", f"{f}: imported by the server or CLI")
                    sel.specs_all.append(f"{f}: imported by the server")

        if f.startswith("web/"):
            hit = True
            w = f.removeprefix("web/")
            if f in WEB_ALL:
                sel.vitest_all.append(f)
                sel.specs_all.append(f"{f}: web build or test config")
            elif w.startswith("tests/e2e/"):
                if w.endswith(".spec.ts"):
                    sel.specs[w].append(f"{f}: changed spec")
                else:
                    sel.specs_all.append(f"{f}: shared browser test helper")
            elif w.startswith(("src/", "tests/unit/", "index.html", "public/")):
                if (REPO / f).exists() and w.endswith((".ts", ".tsx")):
                    sel.vitest.append(w)
                elif w.startswith(("src/", "tests/unit/")):
                    sel.vitest_all.append(f"{f} (deleted or non-TS)")
                if not w.startswith("tests/unit/"):
                    sel.specs_all.append(f"{f}: web app source")
        if not hit:
            sel.unmatched.append(f)
    return sel


def collapse(paths: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop files already covered by a selected directory."""
    dirs = [p for p in paths if not p.endswith(".py")]
    return {p: r for p, r in paths.items()
            if not any(p != d and p.startswith(d.rstrip("/") + "/") for d in dirs)}


def report(files: list[str], sel: Selection) -> None:
    print(f"changed files ({len(files)}):")
    for f in files:
        print(f"  {f}")
    print("\nselected:")
    if sel.python_all:
        print(f"  python: whole suite   because {', '.join(sel.python_all)}")
    else:
        for p, why in sorted(collapse(sel.python).items()):
            print(f"  python: {p:<40} because {'; '.join(why[:3])}{' ...' if len(why) > 3 else ''}")
    if sel.vitest_all:
        print(f"  web-unit: all         because {', '.join(sel.vitest_all[:3])}")
    elif sel.vitest:
        print(f"  web-unit: vitest related {' '.join(sel.vitest)}")
    if sel.specs_all:
        print(f"  browser: all specs    because {'; '.join(dict.fromkeys(sel.specs_all[:3]))}")
    else:
        for s, why in sorted(sel.specs.items()):
            print(f"  browser: {s:<38} because {'; '.join(why[:3])}")
    if sel.demo:
        print(f"  demo: demo/verify.py and demo/verify_examples.py because {', '.join(sel.demo[:3])}")
    if sel.unmatched:
        print(f"  (no tests for: {', '.join(sel.unmatched)})")


def layers(sel: Selection, workers: str) -> list[Layer]:
    out: list[Layer] = []
    py = [] if sel.python_all else sorted(collapse(sel.python))
    if sel.python_all or py:
        heavy = sel.python_all or any(p in ("tests", "tests/e2e") or "/e2e/" in p for p in py) or len(py) > 3
        n = workers if heavy else "0"  # a few unit modules finish before xdist workers would start
        out.append(Layer("python", ["uv", "run", "pytest", "-q", "-m", "not live", "-n", n, "--dist", "loadgroup",
                                    *py]))
    if sel.vitest_all:
        out.append(Layer("web-unit", ["npx", "vitest", "run"], REPO / "web"))
    elif sel.vitest:
        out.append(Layer("web-unit", ["npx", "vitest", "related", "--run", "--passWithNoTests", *sel.vitest],
                         REPO / "web"))
    if sel.specs_all or sel.specs:
        out.append(Layer("browser", ["npm", "run", "--silent", "test:e2e", "--",
                                     *([] if sel.specs_all else sorted(sel.specs))], REPO / "web"))
    if sel.demo:
        out.append(Layer("demo", ["uv", "run", "python", "demo/verify.py"]))
        out.append(Layer("examples", ["uv", "run", "python", "demo/verify_examples.py"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("BASE") or "master", help="branch or commit to diff against")
    ap.add_argument("--files", nargs="+", help="use these changed files instead of asking git")
    ap.add_argument("--dry-run", action="store_true", help="print the selection and the commands, run nothing")
    ap.add_argument("--workers", default=os.environ.get("PYTEST_WORKERS") or str(default_workers()))
    ap.add_argument("--quiet", action="store_true", help="only print the summary and failed layers' output")
    args = ap.parse_args()

    files = sorted(set(args.files)) if args.files else changed_files(args.base)
    sel = select(files, Graph.build())
    report(files, sel)
    todo = layers(sel, args.workers)
    print("\ncommands:" if todo else "\nnothing to run")
    for layer in todo:
        print(f"  [{layer.name}] {' '.join(layer.argv)}" + ("" if layer.cwd == REPO else "  (in web/)"))
    if args.dry_run or not todo:
        return 0
    print(flush=True)
    return run_layers(todo, quiet=args.quiet, title="test-changed")


if __name__ == "__main__":
    sys.exit(main())
