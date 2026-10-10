"""Server tests with fake runtime, executor, provider and improvement logic."""

from __future__ import annotations

import io
import sys
import tarfile
import time
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient

import forge
from forge import db
from forge.types import Event, ExecResult, RunResult

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)

# ---------------------------------------------------------------- fakes


class FakeExecutor:
    instances: list["FakeExecutor"] = []

    def __init__(self, **kw: Any) -> None:
        self.kw, self.seed, self.env, self.stopped = kw, {}, {}, 0
        FakeExecutor.instances.append(self)

    async def start(self, seed_files: dict[str, bytes], env: dict[str, str]) -> None:
        self.seed, self.env = seed_files, env

    async def exec(self, cmd: str, timeout: int = 120) -> ExecResult:
        return ExecResult(exit_code=0, stdout="", stderr="")

    async def read(self, path: str) -> str:
        return self.seed[path].decode()

    async def write(self, path: str, content: str) -> None:
        self.seed[path] = content.encode()

    async def list(self, path: str = ".") -> list[str]:
        return sorted(self.seed)

    async def export_workspace(self, archive_path: str, diff_path: str) -> None:
        with tarfile.open(archive_path, "w:gz"):
            pass
        open(diff_path, "w").write("--- a\n+++ b\n")

    async def stop(self) -> None:
        self.stopped += 1


async def fake_run_agent(version, input, *, run_id, provider, executor, emit, secrets=None, temperature=None,
                         max_turns=30, max_seconds=600, hooks=None, **_) -> RunResult:
    output = f"done {input} | " + ";".join(version.lessons)
    await emit(Event(run_id=run_id, seq=0, ts=time.time(), type="run_started",
                     payload={"version_id": version.id, "model": version.model, "input": input}))
    await emit(Event(run_id=run_id, seq=1, ts=time.time(), type="assistant", payload={"text": output}))
    await emit(Event(run_id=run_id, seq=2, ts=time.time(), type="run_finished",
                     payload={"status": "succeeded", "output": output, "turns": 1, "tokens_in": 3, "tokens_out": 4}))
    return RunResult(status="succeeded", output=output, turns=1, tokens_in=3, tokens_out=4)


class FakeEdit:
    def __init__(self, op: str, text: str, old_text: str | None = None) -> None:
        self.op, self.text, self.old_text = op, text, old_text

    def model_dump(self) -> dict:
        return {"op": self.op, "text": self.text, "old_text": self.old_text, "evidence_run_ids": [], "rationale": "r"}


class FakeImprove(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("forge.improve")
        self.proposals: list[FakeEdit] = []
        self.calls: list[dict] = []

    async def check_case(self, case, executor, output, grader):
        token = case["checker"]
        if token.startswith("NOT:"):
            ok = token[4:] not in output
        else:
            ok = token in output
        return ok, "ok" if ok else f"missing {token}"

    async def propose_lessons(self, version, failures, feedback, grader, cases):
        self.calls.append({"failures": failures, "feedback": feedback, "cases": cases})
        return list(self.proposals)

    def apply_lesson_edits(self, lessons, edits):
        out = list(lessons)
        for e in edits:
            if e.op == "add":
                out.append(e.text)
        return out


def _compare(cases, base, cand):
    summary = {s: {sp: {"passed": 0, "total": 0} for sp in ("train", "holdout")} for s in ("base", "candidate")}
    rows, newly, reg = [], [], []
    for c in cases:
        b = base.get(c["id"], {"passed": False, "reason": "not run", "run_id": ""})
        k = cand.get(c["id"], {"passed": False, "reason": "not run", "run_id": ""})
        for side, r in (("base", b), ("candidate", k)):
            summary[side][c["split"]]["total"] += 1
            summary[side][c["split"]]["passed"] += int(r["passed"])
        if k["passed"] and not b["passed"]:
            newly.append(c["id"])
        if b["passed"] and not k["passed"]:
            reg.append(c["id"])
        rows.append({"case_id": c["id"], "name": c["name"], "split": c["split"], "base": b, "candidate": k})
    tot = lambda s: sum(v["passed"] for v in summary[s].values())  # noqa: E731
    return {**summary, "cases": rows, "newly_passing": newly, "regressions": reg,
            "eligible": tot("candidate") > tot("base") and not reg}


@pytest.fixture
def fake_improve(monkeypatch) -> FakeImprove:
    from forge.improve import majority, summarize_runs  # the real majority-of-k rule

    mod = FakeImprove()
    mod.compare = _compare  # type: ignore[method-assign]
    mod.majority, mod.summarize_runs = majority, summarize_runs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "forge.improve", mod)
    monkeypatch.setattr(forge, "improve", mod, raising=False)
    return mod


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, fake_improve):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_EXECUTOR", "local")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    loop_mod = types.ModuleType("forge.loop")
    loop_mod.run_agent = fake_run_agent  # type: ignore[attr-defined]
    ex_mod = types.ModuleType("forge.executors")
    ex_mod.make_executor = lambda kind, **kw: FakeExecutor(kind=kind, **kw)  # type: ignore[attr-defined]
    prov_mod = types.ModuleType("forge.providers")
    prov_mod.make_provider = lambda model: object()  # type: ignore[attr-defined]
    for name, mod in (("loop", loop_mod), ("executors", ex_mod), ("providers", prov_mod)):
        monkeypatch.setitem(sys.modules, f"forge.{name}", mod)
        monkeypatch.setattr(forge, name, mod, raising=False)
    FakeExecutor.instances.clear()


@pytest.fixture
def client():
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        yield c


def remote():
    from forge.server.app import app

    return TestClient(app, client=REMOTE)


def make_agent(client, slug="bot", **kw) -> dict:
    body = {"slug": slug, "name": "Bot", "system_prompt": "be good", "model": "anthropic:test",
            "seed_files": {"a.txt": "hello"}, **kw}
    r = client.post("/v1/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def poll(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if (v := fn()) is not None:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out")


def add_case(client, slug, name, checker, split="train") -> dict:
    r = client.post(f"/v1/agents/{slug}/evals",
                    json={"name": name, "input": name, "checker_type": "shell", "checker": checker, "split": split})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- tests


def test_create_agent_versions_promote(client):
    a = make_agent(client)
    assert a["api_key"].startswith("fk_") and a["prod_version"] == 1 and a["share_token"]
    assert client.post("/v1/agents", json={"slug": "bot", "name": "x", "system_prompt": "x"}).status_code == 409
    assert [x["slug"] for x in client.get("/v1/agents").json()] == ["bot"]

    v2 = client.post("/v1/agents/bot/versions", json={"lessons": ["L1"]}).json()
    assert v2["n"] == 2 and v2["system_prompt"] == "be good" and v2["seed_files"] == {"a.txt": "hello"}
    assert v2["lessons"] == ["L1"] and v2["model"] == "anthropic:test" and v2["created_by"] == "human"
    assert client.get("/v1/agents/bot/versions/prod").json()["n"] == 1
    assert client.post("/v1/agents/bot/promote", json={"version": 2}).json()["prod_version"] == 2
    assert client.get("/v1/agents/bot/versions/prod").json()["lessons"] == ["L1"]
    assert client.post("/v1/agents/bot/promote", json={"version": 9}).status_code == 404
    got = client.get("/v1/agents/bot").json()
    assert [v["n"] for v in got["versions"]] == [1, 2]
    assert client.patch("/v1/agents/bot", json={"auto_promote": True}).json()["auto_promote"] is True
    assert client.put("/v1/agents/bot/secrets", json={"TOKEN": "s3cret"}).json() == {"names": ["TOKEN"]}
    assert len(client.get("/v1/templates").json()) == 3


def test_run_wait_events_feedback(client):
    make_agent(client)
    client.put("/v1/agents/bot/secrets", json={"TOKEN": "s3cret"})
    r = client.post("/v1/agents/bot/runs", json={"input": "hi", "wait": True, "seed_files": {"b.txt": "x"}}).json()
    assert r["status"] == "succeeded" and r["output"].startswith("done hi") and r["version_n"] == 1
    assert r["source"] == "ui" and r["has_artifacts"] and r["turns"] == 1
    ex = FakeExecutor.instances[-1]
    assert ex.seed == {"a.txt": b"hello", "b.txt": b"x"} and ex.env == {"TOKEN": "s3cret"} and ex.stopped == 1

    evs = client.get(f"/v1/runs/{r['id']}/events.json").json()
    assert [e["type"] for e in evs] == ["run_started", "assistant", "run_finished"]
    with client.stream("GET", f"/v1/runs/{r['id']}/events?after=0") as s:
        body = "".join(s.iter_text())
    assert "event: assistant" in body and "event: run_started" not in body and "event: end" in body
    with client.stream("GET", f"/v1/runs/{r['id']}/events", headers={"Last-Event-ID": "1"}) as s:
        body = "".join(s.iter_text())
    assert "event: assistant" not in body and "event: run_finished" in body

    assert client.get(f"/v1/runs/{r['id']}/diff").text.startswith("---")
    assert client.get(f"/v1/runs/{r['id']}/artifacts").headers["content-type"] == "application/gzip"
    fb = client.post(f"/v1/runs/{r['id']}/feedback", json={"rating": -1, "comment": "too short"}).json()
    assert fb["rating"] == -1 and fb["source"] == "ui"
    assert client.post(f"/v1/runs/{r['id']}/feedback", json={"rating": 5}).status_code == 422
    assert client.get(f"/v1/runs/{r['id']}").json()["feedback"][0]["comment"] == "too short"
    assert client.get("/v1/agents/bot/runs").json()[0]["id"] == r["id"]

    case = client.post(f"/v1/runs/{r['id']}/save-as-eval", json={}).json()
    assert case["checker_type"] == "judge" and "too short" in case["checker"] and case["seed_files"] == {"b.txt": "x"}


def test_async_run_and_multipart_tarball(client):
    make_agent(client)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("repo/README.md", b"readme"), ("repo/bin.dat", b"\0\1\2"), ("repo/../evil", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    r = client.post("/v1/agents/bot/runs", data={"input": "go"},
                    files={"files": ("repo.tar.gz", buf.getvalue(), "application/gzip")}).json()
    assert r["status"] in ("queued", "running", "succeeded")
    poll(lambda: client.get(f"/v1/runs/{r['id']}").json()["status"] == "succeeded" or None)
    assert FakeExecutor.instances[-1].seed == {"a.txt": b"hello", "README.md": b"readme"}


def test_auth(client):
    a = make_agent(client)
    rc = remote()
    assert rc.post("/v1/agents/bot/runs", json={"input": "x"}).status_code == 401
    assert rc.post("/v1/agents/bot/runs", json={"input": "x"},
                   headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert rc.get("/v1/agents").status_code == 401
    r = rc.post(f"/v1/agents/bot/runs?share={a['share_token']}", json={"input": "x", "wait": True})
    assert r.status_code == 200 and r.json()["source"] == "share" and r.json()["status"] == "succeeded"
    hdr = {"Authorization": f"Bearer {a['api_key']}"}
    r2 = rc.post("/v1/agents/bot/runs", json={"input": "y", "wait": True}, headers=hdr).json()
    assert r2["source"] == "api"
    # share token sees only share runs, and cannot use builder routes
    assert [x["id"] for x in rc.get("/v1/agents/bot/runs", headers={"X-Share-Token": a["share_token"]}).json()] \
        == [r.json()["id"]]
    assert rc.post("/v1/agents/bot/versions", json={}, headers={"X-Share-Token": a["share_token"]}).status_code == 401
    assert rc.get("/v1/agents/bot/versions/prod", headers=hdr).status_code == 200
    new_key = rc.post("/v1/agents/bot/rotate-key", headers=hdr).json()["api_key"]
    assert rc.get(f"/v1/runs/{r2['id']}", headers=hdr).status_code == 401
    assert rc.get(f"/v1/runs/{r2['id']}", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200


def test_local_runner(client):
    make_agent(client)
    r = client.post("/v1/runs", json={"agent_slug": "bot", "version_n": 1, "input": "x", "source": "local"}).json()
    assert r["status"] == "running" and r["source"] == "local"
    evs = [{"run_id": r["id"], "seq": 0, "ts": 1.0, "type": "run_started", "payload": {}},
           {"run_id": r["id"], "seq": 1, "ts": 2.0, "type": "run_finished",
            "payload": {"status": "succeeded", "output": "ok", "turns": 2, "tokens_in": 1, "tokens_out": 1}}]
    assert client.post(f"/v1/runs/{r['id']}/events", json=evs).json() == {"ok": True}
    assert client.post(f"/v1/runs/{r['id']}/events", json=evs).json() == {"ok": True}  # idempotent
    got = client.get(f"/v1/runs/{r['id']}").json()
    assert got["status"] == "succeeded" and got["output"] == "ok" and got["turns"] == 2
    assert len(client.get(f"/v1/runs/{r['id']}/events.json").json()) == 2


def test_eval_run_caching(client):
    make_agent(client)
    c1 = add_case(client, "bot", "t1", "done")
    add_case(client, "bot", "h1", "alpha", split="holdout")
    client.post("/v1/agents/bot/evals/run", json={})
    res = poll(lambda: (x := client.get("/v1/agents/bot/evals/results").json())["running"] is False
               and len(x["results"]) == 2 and x or None)
    assert res["train"] == {"passed": 1, "total": 1} and res["holdout"] == {"passed": 0, "total": 1}
    assert res["repeats"] == 3 and sorted((x["passes"], x["runs"]) for x in res["results"]) == [(0, 3), (3, 3)]
    runs_before = len(client.get("/v1/agents/bot/runs").json())
    assert runs_before == 6  # 2 cases x k=3
    client.post("/v1/agents/bot/evals/run", json={"version": 1})
    time.sleep(0.2)
    assert len(client.get("/v1/agents/bot/runs").json()) == runs_before  # cached
    run = client.get("/v1/agents/bot/runs").json()[0]
    assert run["source"] == "eval" and run["eval_case_id"] in {c1["id"], res["results"][1]["case_id"]}
    assert client.delete(f"/v1/evals/{c1['id']}").json() == {"ok": True}
    assert len(client.get("/v1/agents/bot/evals").json()) == 1


def test_eval_repeats_setting_env_default_and_patch(client, monkeypatch):
    """FR9: k defaults to 3, $FORGE_EVAL_REPEATS overrides the default, PATCH sets it per agent (1..9);
    raising k reuses the stored runs and only adds the missing ones."""
    assert make_agent(client)["eval_repeats"] == 3
    monkeypatch.setenv("FORGE_EVAL_REPEATS", "1")
    assert client.get("/v1/agents/bot").json()["eval_repeats"] == 1
    add_case(client, "bot", "t1", "done")
    client.post("/v1/agents/bot/evals/run", json={})
    res = poll(lambda: (x := client.get("/v1/agents/bot/evals/results").json())["running"] is False
               and x["results"] and x or None)
    assert (res["results"][0]["passes"], res["results"][0]["runs"]) == (1, 1)
    assert client.patch("/v1/agents/bot", json={"eval_repeats": 0}).status_code == 422
    assert client.patch("/v1/agents/bot", json={"eval_repeats": 10}).status_code == 422
    assert client.patch("/v1/agents/bot", json={"eval_repeats": 4}).json()["eval_repeats"] == 4
    client.post("/v1/agents/bot/evals/run", json={})
    res = poll(lambda: (x := client.get("/v1/agents/bot/evals/results").json())["running"] is False
               and x["results"][0]["runs"] == 4 and x or None)
    assert res["results"][0]["passes"] == 4 and res["repeats"] == 4
    assert len(client.get("/v1/agents/bot/runs").json()) == 4  # 1 reused + 3 new


def wait_improvement(client, im_id) -> dict:
    return poll(lambda: (x := client.get(f"/v1/improvements/{im_id}").json())["status"] != "running" and x or None)


def test_improve_eligible(client, fake_improve):
    make_agent(client)
    add_case(client, "bot", "t1", "alpha")
    add_case(client, "bot", "h1", "done", split="holdout")
    fake_improve.proposals = [FakeEdit("add", "alpha")]
    im = client.post("/v1/agents/bot/improve", json={}).json()
    assert im["status"] == "running"
    im = wait_improvement(client, im["id"])
    assert im["status"] == "eligible", im
    assert im["base_version"] == 1 and im["candidate_version"] == 2
    assert im["base"]["train"] == {"passed": 0, "total": 1} and im["candidate"]["train"] == {"passed": 1, "total": 1}
    assert len(im["newly_passing"]) == 1 and im["regressions"] == [] and im["lessons_diff"][0]["text"] == "alpha"
    call = fake_improve.calls[0]
    assert len(call["failures"]) == 1 and call["failures"][0]["events"] and [c["split"] for c in call["cases"]] == ["train"]
    v2 = client.get("/v1/agents/bot/versions/2").json()
    assert v2["created_by"] == "optimizer" and v2["lessons"] == ["alpha"] and v2["parent_id"]
    assert client.get("/v1/agents/bot").json()["prod_version"] == 1  # not auto-promoted
    assert client.get("/v1/agents/bot/improvements").json()[0]["id"] == im["id"]


def test_improve_regression_rejected(client, fake_improve):
    make_agent(client)
    add_case(client, "bot", "t1", "alpha")
    add_case(client, "bot", "h1", "NOT:beta", split="holdout")
    fake_improve.proposals = [FakeEdit("add", "alpha beta")]
    im = wait_improvement(client, client.post("/v1/agents/bot/improve", json={}).json()["id"])
    assert im["status"] == "rejected" and len(im["regressions"]) == 1


def test_auto_promote_trigger(client, fake_improve):
    make_agent(client, auto_promote=True)
    add_case(client, "bot", "t1", "alpha")
    fake_improve.proposals = [FakeEdit("add", "alpha")]
    ids = [client.post("/v1/agents/bot/runs", json={"input": f"q{i}", "wait": True}).json()["id"] for i in range(3)]
    for rid in ids[:2]:
        client.post(f"/v1/runs/{rid}/feedback", json={"rating": -1, "comment": "bad"})
    assert client.get("/v1/agents/bot/improvements").json() == []
    client.post(f"/v1/runs/{ids[2]}/feedback", json={"rating": -1, "comment": "bad"})
    ims = client.get("/v1/agents/bot/improvements").json()
    assert len(ims) == 1
    im = wait_improvement(client, ims[0]["id"])
    assert im["status"] == "promoted"
    assert client.get("/v1/agents/bot").json()["prod_version"] == 2
    assert len(fake_improve.calls[0]["feedback"]) == 3
    # the window resets after an improvement
    client.post(f"/v1/runs/{ids[0]}/feedback", json={"rating": -1})
    assert len(client.get("/v1/agents/bot/improvements").json()) == 1


def test_interrupted_runs_marked_failed():
    agent, _ = db.create_agent("old", "Old")
    v = db.create_version(agent["id"], system_prompt="p", model="m")
    r1 = db.create_run(agent["id"], v["id"], "api", "x")
    r2 = db.create_run(agent["id"], v["id"], "api", "y", status="running")
    from forge.server.app import app

    with TestClient(app, client=LOCAL):
        pass
    for rid in (r1["id"], r2["id"]):
        row = db.get_run(rid)
        assert row["status"] == "failed" and row["error"] == "interrupted"


def test_suggested_prompts_persist_and_update(client):
    """Starter prompts are stored on the agent, returned everywhere, and replaced by PATCH or POST versions."""
    prompts = [{"title": "Say hi", "prompt": "Write hi.txt"}, {"title": "Count", "prompt": "Count the files"}]
    a = make_agent(client, suggested_prompts=prompts)
    assert a["suggested_prompts"] == prompts
    assert client.get("/v1/agents/bot").json()["suggested_prompts"] == prompts
    assert client.get("/v1/agents").json()[0]["suggested_prompts"] == prompts

    # An agent created without them returns an empty list.
    assert make_agent(client, slug="plain")["suggested_prompts"] == []

    one = [{"title": "Only", "prompt": "Do one thing"}]
    assert client.patch("/v1/agents/bot", json={"suggested_prompts": one}).json()["suggested_prompts"] == one
    # PATCH without the field keeps them.
    assert client.patch("/v1/agents/bot", json={"name": "Bot 2"}).json()["suggested_prompts"] == one

    v2 = client.post("/v1/agents/bot/versions", json={"lessons": ["L"], "suggested_prompts": prompts}).json()
    assert v2["n"] == 2 and "suggested_prompts" not in v2
    assert client.get("/v1/agents/bot").json()["suggested_prompts"] == prompts
    assert client.patch("/v1/agents/bot", json={"suggested_prompts": []}).json()["suggested_prompts"] == []

    bad = client.patch("/v1/agents/bot", json={"suggested_prompts": [{"title": "", "prompt": "x"}]})
    assert bad.status_code == 422
    too_many = [{"title": f"t{i}", "prompt": "p"} for i in range(13)]
    assert client.patch("/v1/agents/bot", json={"suggested_prompts": too_many}).status_code == 422


def test_suggested_prompts_migration_adds_column(tmp_path, monkeypatch):
    """A database created before suggested_prompts existed gains the column in place."""
    import sqlite3

    data = tmp_path / "old"
    data.mkdir()
    conn = sqlite3.connect(data / "forge.db")
    conn.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                 "description TEXT NOT NULL DEFAULT '', prod_version_id TEXT, auto_promote INTEGER NOT NULL DEFAULT 0, "
                 "api_key_hash TEXT NOT NULL, share_token TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)")
    conn.execute("INSERT INTO agents VALUES ('ag_1', 'old', 'Old', '', NULL, 0, 'h', 't', 0)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("FORGE_DATA_DIR", str(data))
    assert db.get_agent_by_slug("old")["suggested_prompts"] == []
    db.update_agent("ag_1", suggested_prompts=[{"title": "a", "prompt": "b"}])
    assert db.get_agent("ag_1")["suggested_prompts"] == [{"title": "a", "prompt": "b"}]
