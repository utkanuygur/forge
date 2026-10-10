"""forge.buildinfo resolution order: environment, then the bundled build-info.json, then git; never fails."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from forge import buildinfo

NO_ENV: dict[str, str] = {}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "HOME": str(repo), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.txt").write_text("a")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", "one")
    buildinfo.from_git.cache_clear()
    yield r
    buildinfo.from_git.cache_clear()


def head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_env_wins_over_file_and_git(tmp_path, repo):
    f = tmp_path / "build-info.json"
    f.write_text(json.dumps({"version": "0.1.2", "commit": "fff0000", "built_at": "2026-01-01T00:00:00Z"}))
    env = {"FORGE_VERSION": "0.1.3", "FORGE_COMMIT": "473b4fa", "FORGE_BUILT_AT": "2026-10-10T12:00:00Z",
           "FORGE_CHANNEL": "release"}
    assert buildinfo.build_info(env=env, file=f, repo=repo) == {
        "version": "0.1.3", "commit": "473b4fa", "built_at": "2026-10-10T12:00:00Z", "channel": "release"}


def test_env_preview_channel_follows_version_and_file_fills_gaps(tmp_path):
    f = tmp_path / "build-info.json"
    f.write_text(json.dumps({"version": "0.1.0", "commit": "abc1234", "built_at": "2026-10-09T08:00:00Z"}))
    info = buildinfo.build_info(env={"FORGE_VERSION": "0.1.4-preview"}, file=f, repo=tmp_path / "none")
    assert info == {"version": "0.1.4-preview", "commit": "abc1234", "built_at": "2026-10-09T08:00:00Z",
                    "channel": "preview"}


def test_file_is_used_without_env(tmp_path, repo):
    f = tmp_path / "build-info.json"
    f.write_text(json.dumps({"version": "0.1.3", "commit": "473b4fa", "built_at": "2026-10-10T12:00:00Z",
                             "channel": "release", "extra": "ignored"}))
    info = buildinfo.build_info(env=NO_ENV, file=f, repo=repo)
    assert info == {"version": "0.1.3", "commit": "473b4fa", "built_at": "2026-10-10T12:00:00Z",
                    "channel": "release"}


def test_git_for_dev_checkouts(tmp_path, repo):
    missing = tmp_path / "missing.json"
    info = buildinfo.build_info(env=NO_ENV, file=missing, repo=repo)
    assert info == {"version": None, "commit": head(repo), "built_at": None, "channel": "dev"}  # no tag yet

    git(repo, "tag", "v0.1.2")
    (repo / "b.txt").write_text("b")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "two")
    (repo / "a.txt").write_text("dirty")
    buildinfo.from_git.cache_clear()
    info = buildinfo.build_info(env=NO_ENV, file=missing, repo=repo)
    assert info == {"version": "0.1.2+dev", "commit": head(repo), "built_at": None, "channel": "dev"}


def test_git_result_is_cached(tmp_path, repo):
    first = buildinfo.from_git(repo)
    git(repo, "tag", "v9.9.9")
    assert buildinfo.from_git(repo) == first
    buildinfo.from_git.cache_clear()
    assert buildinfo.from_git(repo)["version"] == "9.9.9+dev"


def test_frozen_server_skips_git(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert buildinfo.build_info(env=NO_ENV, file=tmp_path / "missing.json", repo=repo) == dict.fromkeys(
        buildinfo.FIELDS)


def test_failures_become_null(tmp_path, monkeypatch):
    bad = tmp_path / "build-info.json"
    bad.write_text("{not json")
    assert buildinfo.build_info(env=NO_ENV, file=bad, repo=tmp_path) == dict.fromkeys(buildinfo.FIELDS)
    bad.write_text("[1, 2]")
    assert buildinfo.build_info(env=NO_ENV, file=bad, repo=tmp_path)["version"] is None

    # git missing or broken
    (tmp_path / ".git").mkdir()
    buildinfo.from_git.cache_clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert buildinfo.build_info(env=NO_ENV, file=bad, repo=tmp_path) == dict.fromkeys(buildinfo.FIELDS)
    buildinfo.from_git.cache_clear()

    # an unknown channel is dropped, then derived from the version
    info = buildinfo.build_info(env={"FORGE_VERSION": "0.1.3", "FORGE_CHANNEL": "nightly"}, file=bad,
                                repo=tmp_path / "none")
    assert info["channel"] == "release"

    # a source that raises is skipped
    monkeypatch.setattr(buildinfo, "from_file", lambda path=None: 1 / 0)
    assert buildinfo.build_info(env={"FORGE_COMMIT": "abc1234"}, repo=tmp_path / "none")["commit"] == "abc1234"


def test_version_endpoint_and_healthz(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.setenv("FORGE_SCHEDULER", "0")
    monkeypatch.setenv("FORGE_VERSION", "0.1.4-preview")
    monkeypatch.setenv("FORGE_COMMIT", "abc1234")
    monkeypatch.setenv("FORGE_BUILT_AT", "2026-10-10T12:00:00Z")
    monkeypatch.delenv("FORGE_CHANNEL", raising=False)
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server import app as app_module

    want = {"version": "0.1.4-preview", "commit": "abc1234", "built_at": "2026-10-10T12:00:00Z", "channel": "preview"}
    with TestClient(app_module.app, client=("203.0.113.9", 5000)) as c:  # a remote client: no auth needed
        r = c.get("/v1/version")
        assert r.status_code == 200 and r.json() == want
        assert c.get("/healthz").json()["build"] == want
