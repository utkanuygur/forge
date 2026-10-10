"""Build the docs-writer example: agent.json, evals.json and prompt_checks.json.

    uv run python demo/examples/docs-writer/build.py [--out DIR]

The seed (seed/) is a small hand written library, shelfcache: an LRU cache with per entry expiry,
a memoizing decorator and JSON persistence, with docstrings (a few deliberately missing), tests and a
CHANGELOG whose Unreleased section has two breaking changes, new features, a deprecation and fixes.
Its README is a stub. Prose is graded by judge rubrics with concrete pass or fail criteria; the parts
that can be checked mechanically (the README quickstart and the API page examples actually run, every
parameter is documented, docstrings are added without changing code) have shell checkers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import case, py_checker, write_json  # noqa: E402

SYSTEM_PROMPT = """\
You are a technical writer embedded in a Python library team. The library's repository is checked out \
in your workspace.

How to work:
1. Read the code before writing about it: the modules, their docstrings, the tests (they show real usage) \
and the CHANGELOG. Documentation must match the code exactly: names, signatures, defaults, return values and \
the exceptions raised. Never document behavior you have not confirmed in the source.
2. Every code example must run as written against the code in this repository. Run each one (with \
`PYTHONPATH=. python3 example.py` or similar) before you finish, and fix the example, never the library, if \
it fails.
3. Write for the reader named in the request: lead with what they need, keep it concise, use Markdown \
headings, fenced code blocks marked `python`, and tables where they help.
4. Do not change library code unless the request asks for it. Finish with a short summary of the files you \
wrote and how you verified the examples.

You have no network access, and the package is not installed: import it from the repository root."""

SUGGESTED = [
    {
        "title": "Add missing docstrings",
        "prompt": "Some public functions and methods in shelfcache have no docstring. Add a concise Google "
                  "style docstring (summary, Args, Returns, Raises where relevant) to every public function, "
                  "class and method that lacks one. Do not change any code, only add docstrings, and make "
                  "sure the tests still pass.",
    },
    {
        "title": "Write a runnable example",
        "prompt": "Add an examples/ directory with one runnable script that shows a realistic use of "
                  "shelfcache: memoize a slow function with @cached and a ttl, show a cache hit, and print the "
                  "cache stats. It must run from the repository root with `PYTHONPATH=. python3 "
                  "examples/<name>.py` in under 10 seconds and print what it demonstrates.",
    },
    {
        "title": "Write a contributing guide",
        "prompt": "Write CONTRIBUTING.md for new contributors: how to set up a development environment, run the "
                  "tests, the docstring style the code uses, and how to record a change in CHANGELOG.md "
                  "(follow the format the file already uses). Keep it to one page.",
    },
]

README_PROMPT = """\
shelfcache's README is a stub. Write README.md for developers evaluating the library: a one paragraph \
description, installation (`pip install shelfcache`), and a "Quickstart" section with a single ```python \
code block that a reader can paste into a file and run as is. The quickstart must show TTLCache (setting a \
value with a ttl, reading it back, the least recently used eviction) and the @cached decorator, and print \
something so the reader sees it work. Then briefly list the rest of the API with links to where it is \
documented in the code."""

README_CHECK = r"""
import doctest, subprocess, tempfile
check(os.path.isfile("README.md"), "README.md was not written")
text = open("README.md").read()
check(re.search(r"pip install\s+shelfcache", text), "README.md does not show `pip install shelfcache`")
section = md_section(text, r"^quick\s*-?\s*start\b")
check(section is not None, "README.md has no Quickstart heading")
blocks = python_blocks(section)
check(blocks, "the Quickstart section has no ```python code block")
def runnable(block):
    if re.search(r"^\s*>>> ", block, re.M):
        return "\n".join(ex.source for ex in doctest.DocTestParser().get_examples(block))
    return block
code = "\n".join(runnable(b) for b in blocks)
check(re.search(r"\b(from shelfcache import|import shelfcache)\b", code), "the quickstart does not import shelfcache")
check("TTLCache" in code, "the quickstart does not use TTLCache")
check(re.search(r"@(shelfcache\.)?cached\b", code), "the quickstart does not use the @cached decorator")
check("print(" in code, "the quickstart prints nothing")
scratch = tempfile.mkdtemp()
with open(os.path.join(scratch, "quickstart.py"), "w") as f:
    f.write(code)
try:
    r = subprocess.run([sys.executable, "quickstart.py"], cwd=scratch, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": os.getcwd()})
except subprocess.TimeoutExpired:
    fail("the quickstart code did not finish within 60 seconds")
check(r.returncode == 0, "the quickstart code fails when run: " + (r.stderr.strip().splitlines() or ["?"])[-1])
check(r.stdout.strip(), "the quickstart code runs but prints nothing")
"""

README_RUBRIC = """\
Grade README.md in the workspace diff as the front page for developers evaluating shelfcache. Pass only if every point holds:
1. It opens with a one paragraph description that says what the library is (in process caches with least recently used eviction and per entry time to live, plus a memoizing decorator and JSON persistence) without marketing filler.
2. It shows installation with `pip install shelfcache` and states the supported Python version from pyproject.toml (3.10 or newer).
3. It has a Quickstart section with one ```python block that uses only the real API with correct signatures: `TTLCache(maxsize=..., ttl=...)` (clock is keyword only), `cache.set(key, value, ttl=...)` with ttl keyword only, `get`, and `@cached(maxsize, ttl=...)` with ttl keyword only. Using a positional ttl in `cached(...)` or `set(...)`, or any function or method that does not exist in the code, is a failure.
4. The quickstart demonstrates least recently used eviction correctly (with maxsize reached, the entry that was least recently read or written is the one removed) and its comments or printed output describe the behavior truthfully.
5. It briefly lists the rest of the public API (CacheStats and stats(), purge(), remaining_ttl(), delete() or pop(), dump() and load()) with accurate one line descriptions.
6. It is concise (roughly under 120 lines) and contains no TODO placeholders."""

API_CACHE_PROMPT = """\
Write the API reference page for the shelfcache.cache module at docs/api/cache.md. Cover every public class, \
method and attribute: the exact signature with defaults, what it does, what it returns, and what it raises. \
Explain the eviction and expiry rules precisely (what counts as a hit or a miss, when expired entries are \
removed, what reading does to recency). Include at least one short runnable example."""

API_CACHE_RUBRIC = """\
Grade docs/api/cache.md in the workspace diff as the API reference for shelfcache.cache. Pass only if every point holds:
1. It documents `TTLCache(maxsize: int = 128, ttl: float | None = None, *, clock=time.monotonic)` with the correct defaults, says clock is keyword only, and that ValueError is raised when maxsize is less than 1 or ttl is not positive.
2. It documents each public method with a correct signature and return value: `get(key, default=None)`, `set(key, value, *, ttl=None)` (ttl keyword only, overrides the default for that entry, ValueError if not positive), `delete(key) -> bool`, `pop(key[, default])` (KeyError without a default), `clear()` (also resets the stats counters), `purge() -> int` (number removed), `expire()` (deprecated alias of purge that emits DeprecationWarning, removal in 0.6.0), `stats() -> CacheStats`, and `remaining_ttl(key)` (None for entries that never expire, KeyError if missing or expired).
3. It documents `CacheStats` as a named tuple with the fields hits, misses, evictions and expirations.
4. It states the rules correctly: setting a new key into a full cache evicts the least recently used entry; get and indexing mark an entry as recently used; an expired entry is removed when it is read and counts as a miss, not a hit; `in` and `len` behave as the code does (len counts entries not yet purged).
5. It documents the mapping operations `cache[key]` (KeyError when missing), `cache[key] = value`, `del cache[key]` and `key in cache`.
6. Its example uses the real API correctly. It invents no methods or parameters that are not in the code."""

API_SERIALIZE_PROMPT = """\
Write the API reference page for the shelfcache.serialize module at docs/api/serialize.md: every public \
function with its exact signature and defaults, each parameter, the return value, the exceptions it raises, \
and the file format. Include at least one ```python example that saves a cache and loads it back; it must \
run as written from any directory with the repository on PYTHONPATH."""

API_SERIALIZE_CHECK = r"""
import inspect, subprocess, tempfile
sys.path.insert(0, os.getcwd())
r = subprocess.run(GIT + ["diff", "--quiet", "baseline", "--", "shelfcache"], capture_output=True)
check(r.returncode == 0, "the library code under shelfcache/ was changed; the task only asks for documentation")
import shelfcache.serialize as mod
path = "docs/api/serialize.md"
check(os.path.isfile(path), f"{path} was not written")
text = open(path).read()
for name in ("dump", "load"):
    fn = getattr(mod, name)
    check(re.search(r"\b%s\s*\(" % name, text), f"{path} does not document {name}()")
    for p in inspect.signature(fn).parameters:
        check(re.search(r"\b%s\b" % p, text), f"{path} does not document the {p!r} parameter of {name}()")
for exc in ("TypeError", "ValueError"):
    check(exc in text, f"{path} does not mention {exc}, which the functions raise")
check(re.search(r"\bTTLCache\b", text), f"{path} does not say that load() returns a TTLCache")
blocks = python_blocks(text)
check(blocks, f"{path} has no ```python example")
check(any("dump(" in b and "load(" in b for b in blocks) or ("dump(" in "".join(blocks) and "load(" in "".join(blocks)),
      "the examples do not both dump and load a cache")
for i, block in enumerate(blocks, 1):
    scratch = tempfile.mkdtemp()
    with open(os.path.join(scratch, "example.py"), "w") as f:
        f.write(block)
    try:
        r = subprocess.run([sys.executable, "example.py"], cwd=scratch, capture_output=True, text=True, timeout=60,
                           env={**os.environ, "PYTHONPATH": os.getcwd()})
    except subprocess.TimeoutExpired:
        fail(f"example {i} did not finish within 60 seconds")
    check(r.returncode == 0, f"example {i} in {path} fails when run: " + (r.stderr.strip().splitlines() or ["?"])[-1])
"""

RELEASE_PROMPT = """\
We are about to release shelfcache 0.5.0. Write RELEASE_NOTES.md from the Unreleased section of \
CHANGELOG.md, for users upgrading from 0.4. Lead with what they must change, show before and after code for \
each breaking change, then the new features, deprecations and fixes. Do not edit CHANGELOG.md."""

RELEASE_RUBRIC = """\
Grade RELEASE_NOTES.md in the workspace diff as upgrade notes for shelfcache 0.5.0, written from the Unreleased section of CHANGELOG.md. Pass only if every point holds:
1. It is titled for version 0.5.0 and the breaking changes come first, before new features.
2. It covers both breaking changes with correct before and after code: `cached()` takes ttl as keyword only (for example `@cached(256, 30)` becomes `@cached(256, ttl=30)`), and `TTLCache.stats()` returns a CacheStats named tuple (`stats()["hits"]` becomes `stats().hits`, with `stats()._asdict()` for the old dict).
3. It lists every added item: per entry ttl in `set()`, `purge()`, `remaining_ttl(key)`, the `key` function for `cached()`, and the new `expirations` counter.
4. It states the deprecation: `TTLCache.expire()` is an alias of `purge()` that warns and will be removed in 0.6.0, and tells users to switch to purge().
5. It lists both fixes: an expired entry read with get() no longer counts as a hit, and dump() writes the file atomically.
6. It invents nothing that is not in the changelog (no extra features, dates presented as fact, or performance claims), and CHANGELOG.md is unchanged in the diff.
7. It is concise and scannable: headings or bullet lists, no more than about 80 lines."""

DOCSTRINGS_CHECK = r"""
import ast, glob, subprocess
def strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)
missing = []
for path in sorted(glob.glob("shelfcache/*.py")):
    src = open(path).read()
    tree = ast.parse(src)
    def visit(nodes, prefix=""):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                if not ast.get_docstring(n):
                    missing.append(f"{path}:{prefix}{n.name}")
                if isinstance(n, ast.ClassDef):
                    visit(n.body, n.name + ".")
    visit(tree.body)
    old = subprocess.run(GIT + ["show", "baseline:" + path], capture_output=True, text=True)
    check(old.returncode == 0, f"cannot read the baseline of {path}")
    check(strip_docstrings(ast.parse(old.stdout)) == strip_docstrings(ast.parse(src)), f"{path}: code changed, not only docstrings")
check(not missing, "public definitions without a docstring: " + ", ".join(missing))
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], capture_output=True, text=True)
check(r.returncode == 0, "tests fail: " + r.stdout.strip()[-300:])
"""

EXAMPLE_CHECK = r"""
import glob, subprocess, time
scripts = sorted(glob.glob("examples/*.py"))
check(scripts, "no examples/*.py script was written")
for path in scripts:
    code = open(path).read()
    check("shelfcache" in code, f"{path} does not use shelfcache")
    start = time.time()
    try:
        r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONPATH": os.getcwd()})
    except subprocess.TimeoutExpired:
        fail(f"{path} did not finish within 30 seconds")
    check(r.returncode == 0, f"{path} fails: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    check(time.time() - start < 15, f"{path} took {time.time() - start:.0f}s, asked for under 10")
    check(r.stdout.strip(), f"{path} prints nothing")
joined = "".join(open(p).read() for p in scripts)
check(re.search(r"@(shelfcache\.)?cached\(", joined) and "ttl" in joined, "the example does not use @cached with a ttl")
check("stats()" in joined, "the example does not print the cache stats")
"""

CONTRIBUTING_CHECK = r"""
check(os.path.isfile("CONTRIBUTING.md"), "CONTRIBUTING.md was not written")
text = open("CONTRIBUTING.md").read()
for needle in ("pytest", "CHANGELOG", "Unreleased"):
    check(needle.lower() in text.lower(), f"CONTRIBUTING.md does not mention {needle}")
check(re.search(r"google", text, re.I) or "Args:" in text, "CONTRIBUTING.md does not describe the docstring style (Google style, Args/Returns/Raises)")
check(len(text.splitlines()) <= 150, "CONTRIBUTING.md is longer than one page")
"""


def agent_spec() -> dict:
    return {
        "slug": "docs-writer",
        "name": "Docs writer",
        "description": "Writes READMEs, API references and release notes for a Python library, with examples "
                       "that run against the real code.",
        "system_prompt": SYSTEM_PROMPT,
        "tools": ["bash", "read_file", "write_file", "edit_file"],
        "network": False,
        "suggested_prompts": SUGGESTED,
    }


def build(out: Path) -> None:
    cases = [
        case("readme-quickstart-runs", "train", README_PROMPT, py_checker(README_CHECK)),
        case("readme-quickstart-quality", "train", README_PROMPT, README_RUBRIC, "judge"),
        case("api-docs-cache", "train", API_CACHE_PROMPT, API_CACHE_RUBRIC, "judge"),
        case("api-docs-serialize", "holdout", API_SERIALIZE_PROMPT, py_checker(API_SERIALIZE_CHECK)),
        case("release-notes-0-5", "holdout", RELEASE_PROMPT, RELEASE_RUBRIC, "judge"),
    ]
    write_json(out / "evals.json", cases)
    write_json(out / "agent.json", agent_spec())
    write_json(out / "prompt_checks.json", {
        "Add missing docstrings": py_checker(DOCSTRINGS_CHECK),
        "Write a runnable example": py_checker(EXAMPLE_CHECK),
        "Write a contributing guide": py_checker(CONTRIBUTING_CHECK),
    })


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()
    build(args.out)
    print(f"docs-writer: wrote evals.json, agent.json and prompt_checks.json under {args.out}")


if __name__ == "__main__":
    main()
