"""Workspace file browsing: safe tar reading, listing, changed marking and the /runs/{id}/files endpoints."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge import db
from forge.server import files

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


def build_tar(path: Path, entries: list[tuple]) -> Path:
    """entries: ("file", name, bytes) | ("sym", name, target) | ("hard", name, target) | ("dir", name)."""
    with tarfile.open(path, "w:gz") as tar:
        for kind, name, *rest in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(rest[0])
                tar.addfile(info, io.BytesIO(rest[0]))
                continue
            if kind == "dir":
                info.type = tarfile.DIRTYPE
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, rest[0]
            elif kind == "hard":
                info.type, info.linkname = tarfile.LNKTYPE, rest[0]
            tar.addfile(info)
    return path


WORKSPACE = [
    ("dir", "."),
    ("dir", "./src"),
    ("file", "./hello.py", b"print('hello from forge')\n"),
    ("file", "./src/app.py", b"def main():\n    return 1\n"),
    ("file", "./logo.png", b"\x89PNG\r\n\x1a\n\0\0\0binary"),
    ("file", "./.git/config", b"[core]\n"),
    ("file", "./.git/objects/ab/cd", b"x"),
    ("file", "./big.txt", b"a" * (files.MAX_TEXT_BYTES + 10)),
    ("file", "./latin.txt", "café ✓\n".encode()),
    ("sym", "./link.py", "src/app.py"),
    ("sym", "./src/up.py", "../hello.py"),
    ("sym", "./escape", "../../etc/passwd"),
    ("sym", "./abs", "/etc/passwd"),
    ("sym", "./loop1", "loop2"),
    ("sym", "./loop2", "loop1"),
    ("sym", "./dangling", "nope.txt"),
    ("sym", "./gitlink", ".git/config"),
    ("hard", "./hard.py", "./hello.py"),
    ("file", "../outside.txt", b"evil"),
    ("file", "/abs/path.txt", b"evil"),
]


@pytest.fixture
def archive(tmp_path) -> files.Archive:
    return files.Archive(str(build_tar(tmp_path / "ws.tar.gz", WORKSPACE)))


# ---------------------------------------------------------------- tar reading safety


@pytest.mark.parametrize("raw,expected", [
    ("hello.py", "hello.py"), ("./src//app.py", "src/app.py"), ("src/./app.py", "src/app.py"),
    ("../x", None), ("src/../../x", None), ("/etc/passwd", None), (".git/config", None), ("a/.git/HEAD", None),
    ("", None), (".", None), ("a\0b", None), ("..\\x", None),
])
def test_normalize(raw, expected):
    assert files.normalize(raw) == expected


def test_listing_skips_git_unsafe_members_and_links_out_of_archive(archive):
    listed = {e.path: e for e in archive.entries()}
    assert sorted(listed) == ["big.txt", "hard.py", "hello.py", "latin.txt", "link.py", "logo.png", "src/app.py",
                              "src/up.py"]
    assert listed["hello.py"].size == 26 and listed["hello.py"].is_text
    assert listed["latin.txt"].is_text and not listed["logo.png"].is_text
    # Links report the size and type of the file they resolve to.
    assert listed["link.py"].size == listed["src/app.py"].size and listed["link.py"].is_text
    assert listed["hard.py"].size == 26


def test_read_text_and_in_archive_links(archive):
    assert archive.read("hello.py") == b"print('hello from forge')\n"
    assert archive.read("./src/app.py").startswith(b"def main")
    assert archive.read("link.py") == archive.read("src/app.py")
    assert archive.read("src/up.py") == archive.read("hello.py") == archive.read("hard.py")
    assert archive.read("latin.txt").decode() == "café ✓\n"


@pytest.mark.parametrize("path,status", [
    ("logo.png", 415), ("big.txt", 413), ("missing.txt", 404), ("escape", 404), ("abs", 404), ("loop1", 404),
    ("dangling", 404), ("gitlink", 404), (".git/config", 400), ("../outside.txt", 400), ("/abs/path.txt", 400),
    ("outside.txt", 404), ("src/../../etc/passwd", 400),
])
def test_read_rejects(archive, path, status):
    with pytest.raises(files.FileError) as exc:
        archive.read(path)
    assert exc.value.status == status


def test_open_archive_caches_until_the_file_changes(tmp_path):
    p = build_tar(tmp_path / "a.tar.gz", [("file", "./a.txt", b"1")])
    first = files.open_archive(str(p))
    assert files.open_archive(str(p)) is first
    build_tar(p, [("file", "./a.txt", b"1"), ("file", "./b.txt", b"22")])
    assert [e.path for e in files.open_archive(str(p)).entries()] == ["a.txt", "b.txt"]


def test_unreadable_archive_is_a_404(tmp_path):
    p = tmp_path / "bad.tar.gz"
    p.write_bytes(b"not a tarball")
    with pytest.raises(files.FileError) as exc:
        files.open_archive(str(p))
    assert exc.value.status == 404


# ---------------------------------------------------------------- changed files from the diff

DIFF = """diff --git a/hello.py b/hello.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/hello.py
@@ -0,0 +1 @@
+print('hello from forge')
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
--- not a header, a removed line
+++ not a header either
diff --git a/old.txt b/old.txt
deleted file mode 100644
index 3333333..0000000
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/a.txt b/b.txt
similarity index 100%
rename from a.txt
rename to b.txt
diff --git a/logo.png b/logo.png
new file mode 100644
index 0000000..4444444
GIT binary patch
literal 4
LcmZ?wbhEX0

diff --git a/my file.txt b/my file.txt
index 5..6 100644
--- a/my file.txt
+++ b/my file.txt
@@ -1 +1 @@
-x
+y
diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"
new file mode 100644
--- /dev/null
+++ "b/caf\\303\\251.txt"
@@ -0,0 +1 @@
+z
"""


def test_changed_files_parses_git_diff():
    assert files.changed_files(DIFF) == {
        "hello.py": "added", "src/app.py": "modified", "old.txt": "deleted", "b.txt": "renamed",
        "logo.png": "added", "my file.txt": "modified", "café.txt": "added",
    }
    assert files.changed_files("") == {}


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        a = c.post("/v1/agents", json={"slug": "bot", "name": "Bot", "system_prompt": "x", "model": "fake:hello"})
        assert a.status_code == 200, a.text
        agent = a.json()
        version = db.get_version_n(agent["id"], 1)

        def make_run(source: str = "api", *, archive: bool = True, diff: str | None = DIFF) -> str:
            run = db.create_run(agent["id"], version["id"], source, "go")
            d = db.run_dir(run["id"])
            d.mkdir(parents=True, exist_ok=True)
            fields = {"status": "succeeded"}
            if archive:
                fields["artifact_path"] = str(build_tar(d / "workspace.tar.gz", WORKSPACE))
            if diff is not None:
                (d / "diff.patch").write_text(diff)
                fields["diff_path"] = str(d / "diff.patch")
            db.update_run(run["id"], **fields)
            return run["id"]

        yield c, agent, make_run


def test_list_files_endpoint_with_changed_marking(server):
    c, _, make_run = server
    rid = make_run()
    plain = c.get(f"/v1/runs/{rid}/files").json()
    assert {"path": "hello.py", "size": 26, "is_text": True} in plain
    assert all(set(x) == {"path", "size", "is_text"} for x in plain)
    marked = {x["path"]: x for x in c.get(f"/v1/runs/{rid}/files?changed=1").json()}
    assert marked["hello.py"]["changed"] and marked["hello.py"]["change"] == "added"
    assert marked["src/app.py"]["change"] == "modified" and marked["logo.png"]["change"] == "added"
    assert marked["link.py"]["changed"] is False and "change" not in marked["link.py"]
    # No stored diff: nothing is marked changed.
    rid2 = make_run(diff=None)
    assert not any(x["changed"] for x in c.get(f"/v1/runs/{rid2}/files?changed=1").json())


def test_read_file_endpoint(server):
    c, _, make_run = server
    rid = make_run()
    r = c.get(f"/v1/runs/{rid}/files/hello.py")
    assert r.status_code == 200 and r.text == "print('hello from forge')\n"
    assert r.headers["content-type"].startswith("text/plain") and r.headers["x-content-type-options"] == "nosniff"
    assert c.get(f"/v1/runs/{rid}/files/src/app.py").text.startswith("def main")
    assert c.get(f"/v1/runs/{rid}/files/logo.png").status_code == 415
    assert c.get(f"/v1/runs/{rid}/files/big.txt").status_code == 413
    assert c.get(f"/v1/runs/{rid}/files/nope.txt").status_code == 404
    assert c.get(f"/v1/runs/{rid}/files/escape").status_code == 404
    assert c.get(f"/v1/runs/{rid}/files/.git/config").status_code == 400
    assert c.get(f"/v1/runs/{rid}/files/src%2F..%2F..%2Fetc%2Fpasswd").status_code == 400
    assert c.get("/v1/runs/r_missing/files").status_code == 404
    assert c.get(f"/v1/runs/{make_run(archive=False)}/files").status_code == 404


def test_files_auth(server):
    c, agent, make_run = server
    from forge.server.app import app

    api_run, share_run = make_run("api"), make_run("share")
    far = TestClient(app, client=REMOTE)
    assert far.get(f"/v1/runs/{api_run}/files").status_code == 401
    assert far.get(f"/v1/runs/{api_run}/files/hello.py").status_code == 401
    key = c.post("/v1/agents/bot/rotate-key").json()["api_key"]
    hdr = {"Authorization": f"Bearer {key}"}
    assert far.get(f"/v1/runs/{api_run}/files", headers=hdr).status_code == 200
    assert far.get(f"/v1/runs/{api_run}/files/hello.py", headers=hdr).status_code == 200
    share = {"share": agent["share_token"]}
    assert far.get(f"/v1/runs/{share_run}/files", params=share).status_code == 200
    assert far.get(f"/v1/runs/{share_run}/files/hello.py", params=share).text.startswith("print")
    # A share token cannot browse runs that were not started from the share page (404: ids are not confirmed).
    assert far.get(f"/v1/runs/{api_run}/files", params=share).status_code == 404
    assert far.get(f"/v1/runs/{api_run}/files/hello.py", params=share).status_code == 404
    assert far.get(f"/v1/runs/{share_run}/files", params={"share": "wrong"}).status_code == 401


SHARE_ONLY_ENDPOINTS = [  # every run-scoped endpoint a share token may call, by family
    ("get", "/v1/runs/{id}", None),                         # run
    ("get", "/v1/runs/{id}/events.json", None),             # events (JSON)
    ("get", "/v1/runs/{id}/events", None),                  # events (SSE)
    ("get", "/v1/runs/{id}/artifacts", None),               # artifacts
    ("get", "/v1/runs/{id}/diff", None),                    # diff
    ("get", "/v1/runs/{id}/files", None),                   # files
    ("get", "/v1/runs/{id}/files/hello.py", None),          # file content
    ("post", "/v1/runs/{id}/feedback", {"rating": 1}),      # feedback
    ("get", "/v1/runs/{id}/approvals", None),               # approvals (governance)
]


@pytest.mark.parametrize("method,path,body", SHARE_ONLY_ENDPOINTS, ids=[p for _, p, _ in SHARE_ONLY_ENDPOINTS])
def test_share_token_reaches_only_share_runs(server, method, path, body):
    """Security: a share token works on runs started from the share page and gets a 404 (not 401) for every
    other run of the same agent, so run ids are not confirmed."""
    c, agent, make_run = server
    from forge.server.app import app

    api_run, share_run = make_run("api"), make_run("share")
    for rid in (api_run, share_run):
        db.append_event(rid, 0, 0.0, "run_finished", {"status": "succeeded"})
    far = TestClient(app, client=REMOTE)
    share = {"share": agent["share_token"]}
    kw = {"json": body} if body is not None else {}
    denied = getattr(far, method)(path.format(id=api_run), params=share, **kw)
    assert denied.status_code == 404, (path, denied.status_code, denied.text[:200])
    allowed = getattr(far, method)(path.format(id=share_run), params=share, **kw)
    assert allowed.status_code == 200, (path, allowed.status_code, allowed.text[:200])
    missing = getattr(far, method)(path.format(id="r_000000000000"), params=share, **kw)
    assert missing.status_code == 404  # indistinguishable from a run that exists but is not a share run


def test_builder_only_run_endpoints_refuse_share_tokens(server):
    """Timeline, cancel and save-as-eval never accept a share token (Playground sessions are builder only too)."""
    c, agent, make_run = server
    from forge.server.app import app

    share_run = make_run("share")
    far = TestClient(app, client=REMOTE)
    share = {"share": agent["share_token"]}
    assert far.get(f"/v1/runs/{share_run}/timeline", params=share).status_code == 401
    assert far.post(f"/v1/runs/{share_run}/cancel", params=share).status_code == 401
    assert far.post(f"/v1/runs/{share_run}/save-as-eval", params=share, json={}).status_code == 401
    assert far.get("/v1/agents/bot/sessions", params=share).status_code == 401
    assert far.post("/v1/agents/bot/sessions", params=share, json={}).status_code == 401
