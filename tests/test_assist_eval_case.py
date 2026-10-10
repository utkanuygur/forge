"""Unit tests for the assist kinds "eval_case" and "eval_cases": context, validation, fake and the route."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from forge import assist, db
from forge.assist import engine, registry
from forge.assist.kinds import eval_case as E
from forge.providers import make_provider

LOCAL = ("127.0.0.1", 5000)

SHELL = 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f out.txt || fail "no out.txt"\necho PASS'
JUDGE = "Grade the reply. Pass only if every point holds:\n1. It names src/app.py.\n2. It gives the count.\nFail if vague."

EXISTING = [
    {"name": "orders-refund-endpoint", "split": "train", "checker_type": "shell",
     "input": "Add a POST /orders/{id}/refund endpoint in src/acme/app.py with tests.", "checker": SHELL},
    {"name": "pricing-docs", "split": "holdout", "checker_type": "judge",
     "input": "Document the pricing module in docs/pricing.md.", "checker": JUDGE},
]


def req(input: dict | None = None, cases: list[dict] | None = None, monkeypatch=None, **kw) -> registry.AssistRequest:
    agent = {"id": "ag_1", "slug": "a", "name": "A", "description": "Maintains the acme orders service."}
    version = {"system_prompt": "You maintain the repo.", "tools": ["bash"], "network": kw.pop("network", False),
               "seed_files": {"README.md": "# Acme orders", "src/acme/app.py": "app = 1\n"}}
    if monkeypatch is not None:
        monkeypatch.setattr(E, "load_cases", lambda agent_id: list(EXISTING if cases is None else cases))
    return registry.AssistRequest(kind="eval_case", agent=agent, version=version, input=input or {},
                                  avoid=kw.pop("avoid", []), secret_names=["GITHUB_TOKEN"])


def case(**kw) -> dict:
    return {"name": "orders-total-endpoint", "input": "Add GET /orders/{id}/total to src/acme/app.py, with a test.",
            "checker_type": "shell", "checker": SHELL, "split": "train", "rationale": "Covers a read endpoint."} | kw


# ---------------------------------------------------------------- context


def test_context_lists_existing_cases_shell_style_request_and_holdout_hint(monkeypatch):
    p = E.build_context(req({"split": "holdout", "checker_type": "shell", "focus": "refunds"}, monkeypatch=monkeypatch))
    assert "[train, shell] orders-refund-endpoint: Add a POST" in p.text
    assert '<existing_shell_checker name="orders-refund-endpoint">' in p.text
    assert "split: holdout" in p.text and "checker_type: shell" in p.text and "focus: refunds" in p.text
    assert "same skills as the train cases" in p.text and "configured secrets: GITHUB_TOKEN" in p.text
    assert "network: off" in p.text
    assert p.data["split"] == "holdout" and p.data["checker_type"] == "shell" and not p.data["network"]
    assert p.data["existing_names"] == ["orders-refund-endpoint", "pricing-docs"]
    assert p.diagnostics["existing_cases"] == 2


def test_context_rejects_bad_options(monkeypatch):
    with pytest.raises(ValueError, match="split"):
        E.build_context(req({"split": "test"}, monkeypatch=monkeypatch))
    with pytest.raises(ValueError, match="checker_type"):
        E.build_context(req({"checker_type": "regex"}, monkeypatch=monkeypatch))
    with pytest.raises(ValueError, match="n must be"):
        E.build_batch_context(req({"n": 7}, monkeypatch=monkeypatch))
    with pytest.raises(ValueError, match="holdout must be"):
        E.build_batch_context(req({"n": 2, "holdout": 3}, monkeypatch=monkeypatch))
    assert E.batch_split({}) == (3, 1) and E.batch_split({"n": 4}) == (4, 2)


def test_context_from_run_includes_input_output_and_feedback(monkeypatch):
    run = {"id": "r_1", "agent_id": "ag_1", "status": "succeeded", "input": "Fix the rounding bug in pricing.",
           "output": "Done, I changed it."}
    monkeypatch.setattr(E, "load_run", lambda rid: (run, [{"rating": -1, "comment": "Tests still fail"}]))
    p = E.build_context(req({"from_run_id": "r_1"}, monkeypatch=monkeypatch))
    assert "<source_run_input>\nFix the rounding bug in pricing.\n</source_run_input>" in p.text
    assert "rating -1: Tests still fail" in p.text and "VERBATIM" in p.text
    assert p.data["fixed_input"] == "Fix the rounding bug in pricing." and p.diagnostics["from_run_id"] == "r_1"
    monkeypatch.setattr(E, "load_run", lambda rid: ({**run, "agent_id": "other"}, []))
    with pytest.raises(ValueError, match="not found for this agent"):
        E.build_context(req({"from_run_id": "r_1"}, monkeypatch=monkeypatch))


def test_context_fixed_task_for_the_checker_field(monkeypatch):
    p = E.build_context(req({"task": "Explain src/acme/app.py"}, monkeypatch=monkeypatch))
    assert "<fixed_input>\nExplain src/acme/app.py\n</fixed_input>" in p.text
    assert p.data["fixed_input"] == "Explain src/acme/app.py"


# ---------------------------------------------------------------- validation


def data(**kw) -> dict:
    return {"split": "train", "checker_type": None, "existing_inputs": [c["input"] for c in EXISTING],
            "existing_names": [c["name"] for c in EXISTING], "avoid": [], "fixed_input": "", "network": False} | kw


def test_validate_accepts_a_good_case_and_flags_each_problem():
    assert E.validate(case(), data()) == []
    assert E.validate(case(checker_type="judge", checker=JUDGE), data()) == []
    errs = lambda **kw: " | ".join(E.validate(case(**kw), data()))  # noqa: E731
    assert "lowercase slug" in errs(name="Bad Name")
    assert "already used" in errs(name="pricing-docs")
    assert "nearly duplicates" in errs(input=EXISTING[0]["input"])
    assert "split must be train" in errs(split="holdout")
    assert "set -euo pipefail" in errs(checker='echo "FAIL: x"; exit 1')
    assert "FAIL" in errs(checker="set -euo pipefail\ntest -f x")
    assert "network access is off" in errs(checker=SHELL + "\ncurl -s https://example.com")
    assert E.validate(case(checker=SHELL + "\ncurl -s https://example.com"), data(network=True)) == []
    assert "Pass only if" in errs(checker_type="judge", checker="Looks good.")
    assert "two concrete" in errs(checker_type="judge", checker="Pass only if it works.")
    assert "rationale is empty" in errs(rationale="")
    assert "must be judge" in " ".join(E.validate(case(), data(checker_type="judge")))
    assert "verbatim" in " ".join(E.validate(case(), data(fixed_input="Something else entirely.")))


def test_validate_batch_counts_splits_and_internal_duplicates():
    d = data(n=2, holdout=1)
    good = {"cases": [case(), case(name="docs-api", split="holdout", checker_type="judge", checker=JUDGE,
                                   input="Write docs/api.md describing every route in src/acme/app.py.")]}
    assert E.validate_batch(good, d) == []
    bad = {"cases": [case(), case(split="train")]}
    errs = " | ".join(E.validate_batch(bad, d))
    assert "exactly 1 holdout" in errs and "case 2: name 'orders-total-endpoint' is already used" in errs
    assert "case 2: input nearly duplicates" in errs
    assert "exactly 2 cases" in " ".join(E.validate_batch({"cases": [case()]}, d))


# ---------------------------------------------------------------- fake and engine


async def test_fake_grader_respects_split_type_existing_and_source_run(monkeypatch):
    fake = make_provider("fake:grader")
    out = await engine.run_assist(E.SPEC, E.build_context(req({"split": "holdout", "checker_type": "shell"},
                                                              monkeypatch=monkeypatch)), fake)
    r = out["result"]
    assert r["split"] == "holdout" and r["checker_type"] == "shell" and r["name"] == E.FAKE_SHELL[0]["name"]
    taken = EXISTING + [{**E.FAKE_SHELL[0], "split": "train", "checker_type": "shell"}]
    out2 = await engine.run_assist(E.SPEC, E.build_context(req({"checker_type": "shell"}, cases=taken,
                                                               monkeypatch=monkeypatch)), fake)
    assert out2["result"]["name"] == E.FAKE_SHELL[1]["name"]
    judge = await engine.run_assist(E.SPEC, E.build_context(req({"checker_type": "judge"},
                                                                monkeypatch=monkeypatch)), fake)
    assert judge["result"]["checker_type"] == "judge" and "Pass only if" in judge["result"]["checker"]
    run = {"id": "r_1", "agent_id": "ag_1", "status": "failed", "input": "Fix the rounding bug.", "output": ""}
    monkeypatch.setattr(E, "load_run", lambda rid: (run, [{"rating": -1, "comment": "Still wrong"}]))
    fr = await engine.run_assist(E.SPEC, E.build_context(req({"from_run_id": "r_1", "checker_type": "judge"},
                                                             monkeypatch=monkeypatch)), fake)
    assert fr["result"]["input"] == "Fix the rounding bug." and "reviewer feedback" in fr["result"]["checker"]


async def test_fake_batch_is_balanced_and_valid(monkeypatch):
    fake = make_provider("fake:grader")
    out = await engine.run_assist(E.BATCH_SPEC, E.build_batch_context(req({"n": 5, "holdout": 2},
                                                                          monkeypatch=monkeypatch)), fake)
    cases = out["result"]["cases"]
    assert [c["split"] for c in cases] == ["train"] * 3 + ["holdout"] * 2
    assert len({c["name"] for c in cases}) == 5


def test_schemas_registered():
    assert {"eval_case", "eval_cases"} <= set(assist.kinds())
    assert registry.envelope_schema(E.SPEC)["properties"]["result"]["required"] == \
        ["name", "input", "checker_type", "checker", "split", "rationale"]


# ---------------------------------------------------------------- HTTP (no Docker)


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


def test_route_eval_case_creates_nothing_and_from_run_404s_for_unknown_run(client):
    r = client.post("/v1/assist/eval_case", json={"slug": "s", "input": {"split": "holdout"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["result"]["split"] == "holdout" and d["kind"] == "eval_case" and d["diagnostics"]["existing_cases"] == 0
    assert client.get("/v1/agents/s/evals").json() == []
    assert client.post("/v1/assist/eval_case", json={"slug": "s", "input": {"from_run_id": "r_nope"}}).status_code == 400
    b = client.post("/v1/assist/eval_cases", json={"slug": "s", "input": {"n": 4}}).json()
    assert [c["split"] for c in b["result"]["cases"]] == ["train", "train", "holdout", "holdout"]
    assert client.get("/v1/agents/s/evals").json() == []
    agent = db.get_agent_by_slug("s")
    with db.connect() as c:
        kinds = [r[0] for r in c.execute("SELECT kind FROM grader_calls WHERE agent_id = ?", (agent["id"],))]
    assert kinds == ["assist:eval_case", "assist:eval_cases"]
    assert json.dumps(d)  # serializable
