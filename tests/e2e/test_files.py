"""End-to-end: workspace file browsing on a real Docker run with the fake:hello model."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e


def test_files_endpoints_on_a_real_run(api, server, make_agent):
    agent = make_agent("files", seed_files={"README.md": "# seed\n", "src/keep.py": "x = 1\n"})
    hdr = {"Authorization": f"Bearer {agent['api_key']}"}
    run = api.post(f"/agents/{agent['slug']}/runs", json={"input": "write hello.py", "wait": True}, headers=hdr).json()
    assert run["status"] == "succeeded", run

    listed = {f["path"]: f for f in api.get(f"/runs/{run['id']}/files", headers=hdr).json()}
    assert {"hello.py", "README.md", "src/keep.py"} <= set(listed)
    assert not any(p == ".git" or p.startswith(".git/") or "/.git/" in p for p in listed)
    assert listed["hello.py"] == {"path": "hello.py", "size": len("print('hello from forge')\n"), "is_text": True}

    marked = {f["path"]: f for f in api.get(f"/runs/{run['id']}/files", params={"changed": 1}, headers=hdr).json()}
    assert marked["hello.py"]["changed"] is True and marked["hello.py"]["change"] == "added"
    assert marked["README.md"]["changed"] is False and marked["src/keep.py"]["changed"] is False

    r = api.get(f"/runs/{run['id']}/files/hello.py", headers=hdr)
    assert r.status_code == 200 and r.text == "print('hello from forge')\n"
    assert r.headers["content-type"].startswith("text/plain")
    assert api.get(f"/runs/{run['id']}/files/src/keep.py", headers=hdr).text == "x = 1\n"
    assert api.get(f"/runs/{run['id']}/files/missing.py", headers=hdr).status_code == 404
    assert api.get(f"/runs/{run['id']}/files/.git/HEAD", headers=hdr).status_code == 400

    # Remote callers without credentials are rejected; the agent's key works from anywhere.
    remote = {"X-Forwarded-For": "203.0.113.9"}
    with httpx.Client(base_url=server.url + "/v1", timeout=30) as c:
        assert c.get(f"/runs/{run['id']}/files", headers=remote).status_code == 401
        assert c.get(f"/runs/{run['id']}/files/hello.py", headers={**remote, **hdr}).status_code == 200
