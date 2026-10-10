"""Unit tests for the configuration and deploy assist kinds: context, validation and the fake:grader reply.

Kinds: instructions, rubric, seed_file, version_note, approval_rules, budget, http_tool, cron, trigger_template.
Every fake reply must pass its own validator (the engine runs it through `check`).
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from forge import assist, db
from forge.assist import engine, registry
from forge.assist.kinds import approval_rules as AR, budget as B, cron as CR, http_tool as H, instructions as I
from forge.assist.kinds import rubric as RB, seed_file as SF, trigger_template as TT, version_note as VN
from forge.providers import make_provider

LOCAL = ("127.0.0.1", 5000)
REAL_SIGNALS = I.load_signals
PROMPT = "You maintain the acme orders service. Follow CONVENTIONS.md and run make check before replying."


def req(kind: str, input: dict | None = None, **version) -> registry.AssistRequest:
    v = {"id": "v_1", "n": 1, "system_prompt": PROMPT, "tools": ["bash", "read_file", "write_file", "edit_file"],
         "network": False, "seed_files": {"README.md": "# Acme", "src/acme/app.py": "app = 1\n"}, "lessons": [],
         "http_tools": [], "approval_policy": {}, "max_run_cost_usd": None, "model": "anthropic:claude-opus-5-5"}
    v.update(version)
    return registry.AssistRequest(kind=kind, agent={"id": "ag_1", "slug": "a", "name": "A", "description": "d",
                                                    "monthly_budget_usd": None},
                                  version=v, input=input or {}, secret_names=["GITHUB_TOKEN"])


async def run_fake(kind: str, r: registry.AssistRequest) -> dict:
    spec = assist.get(kind)
    return await engine.run_assist(spec, spec.build_context(r), make_provider("fake:grader"))


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.setattr(I, "load_signals", lambda agent_id, version_id: (
        ["Fix the bug => The tests still failed"], [{"name": "orders-refund-endpoint", "reason": "exit 1"}],
        ["orders-refund-endpoint"]))
    now = time.time()
    monkeypatch.setattr(B, "load_costs", lambda agent_id, since: (
        [(now - 86400 * d, 0.01 * (d % 5 + 1)) for d in range(20)], 0.05))


def test_every_kind_has_its_own_strict_schema_and_prompt():
    for k in ("instructions", "rubric", "seed_file", "version_note", "approval_rules", "budget", "http_tool", "cron",
              "trigger_template", "eval_case", "eval_cases", "task"):
        spec = assist.get(k)
        assert spec is not None and spec.fake is not None, k
        assert registry.envelope_schema(spec)["properties"]["result"]["additionalProperties"] is False
    assert len({assist.get(k).system_prompt for k in assist.kinds()}) == len(assist.kinds())
    assert assist.get("cron").needs_agent is False and not any(assist.get(k).share_allowed for k in assist.kinds()
                                                               if k != "task")


# ---------------------------------------------------------------- instructions


async def test_instructions_context_validation_and_fake():
    p = I.build_context(req("instructions", {"current": PROMPT, "goal": "stricter about tests"}))
    assert f"<current_instructions>\n{PROMPT}\n</current_instructions>" in p.text
    assert "The tests still failed" in p.text and "orders-refund-endpoint: exit 1" in p.text
    assert "<goal>\nstricter about tests\n</goal>" in p.text and "(the current instructions are given below)" in p.text
    d = p.data
    assert "unchanged" in " ".join(I.validate({"instructions": PROMPT, "summary": "s"}, d))
    assert "mention the evaluation case" in " ".join(I.validate(
        {"instructions": PROMPT + " Pass orders-refund-endpoint.", "summary": "s"}, d))
    assert "summary is empty" in I.validate({"instructions": PROMPT + " More.", "summary": ""}, d)
    long = {"current": "x " * 400, "case_names": []}
    assert "half and twice" in " ".join(I.validate({"instructions": "short new text", "summary": "s"}, long))
    out = await run_fake("instructions", req("instructions", {"current": PROMPT}))
    assert out["result"]["instructions"].startswith(PROMPT) and out["result"]["summary"]
    again = await run_fake("instructions", req("instructions", {"current": out["result"]["instructions"]}))
    assert again["result"]["instructions"] != out["result"]["instructions"]


# ---------------------------------------------------------------- rubric


async def test_rubric_requires_task_validates_and_fake_passes():
    with pytest.raises(ValueError, match="input.task is required"):
        RB.build_context(req("rubric"))
    p = RB.build_context(req("rubric", {"task": "Explain src/acme/app.py"}))
    assert "<task>\nExplain src/acme/app.py\n</task>" in p.text
    assert any("Fail if" in e for e in RB.validate({"rubric": "Pass only if:\n1. a\n2. b"}, {}))
    assert RB.validate({"rubric": "Pass only if every point holds:\n1. a\n2. b\nFail if c."}, {}) == []
    out = await run_fake("rubric", req("rubric", {"task": "Explain src/acme/app.py"}))
    assert "Explain src/acme/app.py" in out["result"]["rubric"]
    nxt = await run_fake("rubric", req("rubric", {"task": "Explain src/acme/app.py", "current": out["result"]["rubric"]}))
    assert nxt["result"]["rubric"] != out["result"]["rubric"]


# ---------------------------------------------------------------- seed_file


async def test_seed_file_validation_and_fake_avoids_existing_paths():
    d = {"existing": ["README.md", "data/sample_orders.csv"], "path": ""}
    ok = {"path": "data/x.csv", "content": "a,b\n1,2\n3,4\n", "description": "d"}
    assert SF.validate(ok, d) == []
    assert "already exists" in " ".join(SF.validate({**ok, "path": "README.md"}, d))
    assert "relative file path" in " ".join(SF.validate({**ok, "path": "../etc/passwd"}, d))
    assert "columns" in " ".join(SF.validate({**ok, "content": "a,b\n1,2\n3\n"}, d))
    assert "does not parse" in " ".join(SF.validate({**ok, "path": "x.json", "content": "{nope"}, d))
    assert "does not parse" in " ".join(SF.validate({**ok, "path": "x.py", "content": "def ("}, d))
    assert "credential" in " ".join(SF.validate({**ok, "path": "x.txt", "content": "key=sk-" + "a" * 30}, d))
    r = req("seed_file", {"description": "orders", "existing": ["data/sample_orders.csv"]})
    out = await run_fake("seed_file", r)
    assert out["result"]["path"] == "data/sample_orders_2.csv" and out["result"]["content"].startswith("order_id,")
    fixed = await run_fake("seed_file", req("seed_file", {"path": "data/orders.csv"}))
    assert fixed["result"]["path"] == "data/orders.csv"


# ---------------------------------------------------------------- version_note


async def test_version_note_diffs_draft_against_base_and_rejects_no_change():
    base = req("version_note").version
    with pytest.raises(ValueError, match="no changes"):
        VN.build_context(req("version_note", {"config": {"system_prompt": PROMPT, "tools": base["tools"]}}))
    with pytest.raises(ValueError, match="input.config"):
        VN.build_context(req("version_note"))
    draft = {"system_prompt": PROMPT + "\nAlways add a CHANGELOG entry.", "network": True,
             "seed_files": {**base["seed_files"], "data/new.csv": "a\n1\n"}, "tools": ["bash"],
             "approval_policy": {"rules": [{"tool": "bash", "pattern": "git push", "mode": "ask"}]}}
    p = VN.build_context(req("version_note", {"config": draft}))
    assert "+Always add a CHANGELOG entry." in p.text and "network access: on (was off)" in p.text
    assert "added ['data/new.csv']" in p.text and "removed ['edit_file', 'read_file', 'write_file']" in p.text
    assert "approval rules: added ['ask bash git push']" in p.text
    assert p.diagnostics["changes"][0] == "instructions changed"
    assert "dashes" in " ".join(VN.validate({"note": "Adds tests - and more"}, {}))
    assert "line breaks" in " ".join(VN.validate({"note": "a\nb"}, {}))
    out = await run_fake("version_note", req("version_note", {"config": draft}))
    assert out["result"]["note"] == "Changes instructions, builtin tools, network access, seed files, approval rules."


# ---------------------------------------------------------------- approval_rules


async def test_approval_rules_validation_and_fake_skips_current_rules():
    p = AR.build_context(req("approval_rules", {"network": True}))
    assert "network: on" in p.text and "(no rules)" in p.text
    d = p.data
    good = {"rules": [{"tool": "bash", "pattern": r"\bgit\s+push\b", "mode": "ask", "label": "git push"}],
            "explanation": "e"}
    assert AR.validate(good, d) == []
    bad_tool = {"rules": [{"tool": "deploy_prod", "pattern": "", "mode": "ask", "label": "x"}], "explanation": "e"}
    assert "has no tool 'deploy_prod'" in " ".join(AR.validate(bad_tool, d))
    bad_re = {"rules": [{"tool": "bash", "pattern": "(", "mode": "ask", "label": "x"}], "explanation": "e"}
    assert "rule 1:" in " ".join(AR.validate(bad_re, d))
    cur = AR.build_context(req("approval_rules", {"current": {"rules": good["rules"]}}))
    assert "already in the policy" in " ".join(AR.validate(good, cur.data))
    out = await run_fake("approval_rules", req("approval_rules", {"current": {"rules": good["rules"]}}))
    tools = [(r["tool"], r["pattern"]) for r in out["result"]["rules"]]
    assert ("bash", r"\bgit\s+push\b") not in tools and ("write_file", "*.env") in tools


# ---------------------------------------------------------------- budget


async def test_budget_stats_validation_and_fake(monkeypatch):
    p = B.build_context(req("budget"))
    st = p.data["stats"]
    assert st["runs"] == 20 and st["max_usd"] == 0.05 and st["p95_usd"] == 0.05
    assert "95th percentile run cost: 0.05 USD" in p.text
    assert "below the 95th percentile" in " ".join(B.validate(
        {"max_run_cost_usd": 0.01, "monthly_budget_usd": 100, "explanation": "e"}, p.data))
    assert "projected monthly spend" in " ".join(B.validate(
        {"max_run_cost_usd": 0.1, "monthly_budget_usd": 0.1, "explanation": "e"}, p.data))
    out = await run_fake("budget", req("budget"))
    r = out["result"]
    assert r["max_run_cost_usd"] == 0.1 and r["monthly_budget_usd"] >= st["monthly_usd"] and r["stats"] == st
    assert B.friendly(0.013) == 0.02 and B.friendly(3.2) == 5 and B.friendly(1) == 1
    monkeypatch.setattr(B, "load_costs", lambda agent_id, since: ([], 0.0))
    with pytest.raises(ValueError, match="no priced runs"):
        B.build_context(req("budget"))


# ---------------------------------------------------------------- http_tool

CURL = ("curl -X POST https://api.acme.dev/v1/orders/{order_id}/refunds -H 'Authorization: Bearer abcd1234secret' "
        "-d '{\"amount_cents\": 500}'")


async def test_http_tool_redacts_literal_credentials_and_validates():
    p = H.build_context(req("http_tool", {"source": CURL}))
    assert "abcd1234secret" not in p.text and "[REDACTED]" in p.text and p.data["secrets"] == ["abcd1234secret"]
    out = await run_fake("http_tool", req("http_tool", {"source": CURL}))
    t = out["result"]
    assert t["name"] == "post_refunds" and t["method"] == "POST" and t["url"].endswith("/{order_id}/refunds")
    assert t["parameters"]["properties"] == {"order_id": {"type": "string", "description": "The order_id."}}
    assert t["headers"] == {"Authorization": "Bearer {{API_TOKEN}}"} and "abcd1234secret" not in json.dumps(out)
    base = {"name": "get_x", "description": "d", "method": "GET", "url": "https://x.dev/{id}",
            "parameters_json": json.dumps({"type": "object", "properties": {"id": {"type": "string"}}}),
            "headers": [], "query_params": []}
    assert H.validate(base, {"existing": [], "secrets": []}) == []
    errs = lambda **kw: " | ".join(H.validate({**base, **kw}, {"existing": ["get_x"], "secrets": ["abcd1234secret"]}))  # noqa: E731
    assert "already exists" in errs()
    assert "not valid JSON" in errs(parameters_json="{")
    assert "is not a parameter" in errs(url="https://x.dev/{other}")
    assert "secret reference" in errs(headers=[{"name": "X-Api-Key", "value": "abcd1234secret"}])
    assert "literal credential" in errs(description="uses abcd1234secret")
    assert "must not carry credentials" in errs(url="https://x.dev/{id}?api_key=zzzzzzzz")
    with pytest.raises(ValueError, match="input.source"):
        H.build_context(req("http_tool"))


# ---------------------------------------------------------------- cron


async def test_cron_parses_validates_and_adds_next_fire_times():
    assert CR.validate({"cron": "61 * * * *", "timezone": "UTC", "explanation": "e"}, {})
    assert "unknown time zone" in " ".join(CR.validate({"cron": "0 9 * * *", "timezone": "Mars/Base",
                                                        "explanation": "e"}, {}))
    r = registry.AssistRequest(kind="cron", input={"text": "weekdays at 9:30am", "timezone": "America/Los_Angeles"})
    out = await run_fake("cron", r)
    res = out["result"]
    assert res["cron"] == "30 9 * * 1-5" and res["timezone"] == "America/Los_Angeles"
    assert len(res["next_fire_times"]) == 3 and res["next_fire_times"] == sorted(res["next_fire_times"])
    assert all(t > time.time() for t in res["next_fire_times"])
    bad_tz = CR.build_context(registry.AssistRequest(kind="cron", input={"text": "hourly", "timezone": "Nope/Nope"}))
    assert bad_tz.diagnostics["default_timezone"] == "UTC"
    for text, expr in (("every 15 minutes", "*/15 * * * *"), ("every hour", "0 * * * *"),
                       ("every monday at 5pm", "0 17 * * 1"), ("daily", "0 9 * * *")):
        assert (await run_fake("cron", registry.AssistRequest(kind="cron", input={"text": text})))["result"]["cron"] == expr


# ---------------------------------------------------------------- trigger_template


async def test_trigger_template_fields_validation_and_fake():
    with pytest.raises(ValueError, match="github or slack"):
        TT.build_context(req("trigger_template", {"source": "email"}))
    with pytest.raises(ValueError, match="input.event"):
        TT.build_context(req("trigger_template", {"source": "slack", "event": "issues"}))
    p = TT.build_context(req("trigger_template", {"source": "github", "event": "issue_comment"}))
    assert "allowed fields:" in p.text and "comment (the text after the command)" in p.text
    assert "unknown placeholders ['nope']" in " ".join(TT.validate({"template": "{nope} {title}", "explanation": "e"},
                                                                   p.data))
    assert "no {field} placeholder" in " ".join(TT.validate({"template": "Do it {{literally}}", "explanation": "e"},
                                                            p.data))
    gh = await run_fake("trigger_template", req("trigger_template", {"source": "github", "event": "pull_request"}))
    assert gh["result"]["template"].startswith("Pull request #{number}")
    sl = await run_fake("trigger_template", req("trigger_template", {"source": "slack", "event": "slash"}))
    again = await run_fake("trigger_template", req("trigger_template", {"source": "slack",
                                                                        "current": sl["result"]["template"]}))
    assert "{text}" in sl["result"]["template"] and again["result"]["template"] != sl["result"]["template"]


# ---------------------------------------------------------------- routes (no Docker)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_GRADER_MODEL", "fake:grader")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        c.post("/v1/agents", json={"slug": "s", "name": "S", "system_prompt": "You help.", "model": "fake:echo",
                                   "seed_files": {"README.md": "# S"}})
        yield c


def test_routes_for_config_kinds_save_nothing(client, monkeypatch):
    monkeypatch.setattr(I, "load_signals", REAL_SIGNALS)
    before = client.get("/v1/agents/s").json()
    r = client.post("/v1/assist/instructions", json={"slug": "s", "input": {"current": "You help."}})
    assert r.status_code == 200, r.text
    assert client.post("/v1/assist/cron", json={"input": {"text": "hourly"}}).json()["result"]["cron"] == "0 * * * *"
    assert client.post("/v1/assist/version_note", json={"slug": "s", "input": {"config": {}}}).status_code == 400
    assert client.post("/v1/assist/rubric", json={"slug": "s", "input": {}}).status_code == 400
    after = client.get("/v1/agents/s").json()
    assert after["prod_version"] == before["prod_version"] and len(after["versions"]) == len(before["versions"])
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) FROM grader_calls WHERE kind = 'assist:cron'").fetchone()[0]
    assert n == 0  # cron without slug is not metered
