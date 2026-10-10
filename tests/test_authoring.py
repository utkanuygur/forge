"""Unit tests for agent drafting (forge.drafting), lesson pruning decisions (forge.pruning) and lesson provenance."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from forge import drafting, pruning
from forge.improve import LessonEdit, apply_lesson_edits_meta
from forge.providers import FAKE_DRAFT, AnthropicProvider, make_provider
from forge.types import Message, ModelResponse, ToolSpec

LOCAL = ("127.0.0.1", 5000)
REMOTE = ("203.0.113.9", 5000)


class ScriptedProvider:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       temperature: float | None = None) -> ModelResponse:
        self.calls.append({"system": system, "messages": messages})
        return ModelResponse(text=self.texts.pop(0))


def good() -> dict:
    return copy.deepcopy(FAKE_DRAFT)


def errors(d: dict) -> list[str]:
    return drafting.validate_draft(drafting.normalize(d))


# ---------------------------------------------------------------- AUTH: validation


def test_fake_draft_is_valid_and_has_two_thirds_train():
    """AUTH2: the documented fake draft passes validation; it has train and holdout cases."""
    assert errors(good()) == []
    splits = [c["split"] for c in FAKE_DRAFT["eval_cases"]]
    assert splits.count("holdout") == 1 and splits.count("train") == 3


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.update(slug="Bad Slug"), "slug"),
    (lambda d: d.update(tools=["bash", "browser"]), "unknown tools"),
    (lambda d: d.update(tools=[]), "tools is empty"),
    (lambda d: d.update(system_prompt="Be helpful."), "system_prompt"),
    (lambda d: d.update(network_reason=""), "network_reason"),
    (lambda d: d["suggested_secrets"].append({"name": "github token", "why": "x"}), "UPPER_SNAKE_CASE"),
    (lambda d: d["eval_cases"][0].update(checker="Is good."), "judge rubric"),
    (lambda d: d["eval_cases"][0].update(checker="x" * 1300), "judge rubric"),
    (lambda d: d["eval_cases"][3].update(split="train"), "holdout"),
    (lambda d: d.update(eval_cases=d["eval_cases"][:2]), "eval cases"),
    (lambda d: d["eval_cases"][1].update(name="crash-report"), "unique"),
    (lambda d: d["eval_cases"][2].update(checker_type="regex"), "checker_type"),
    (lambda d: [c.update(split="holdout") for c in d["eval_cases"][:2]], "two thirds"),
])
def test_validate_draft_rejects(mutate, needle):
    """AUTH2: slug format, known tools, prompt and rubric length, secrets, case count and splits are enforced."""
    d = good()
    mutate(d)
    errs = errors(d)
    assert errs and any(needle in e for e in errs), errs


async def test_draft_retries_once_with_problems_then_succeeds():
    """AUTH3: an invalid first draft triggers exactly one retry that lists the problems; no assistant prefill."""
    bad = good()
    bad["slug"] = "Not A Slug"
    grader = ScriptedProvider("Sure: " + json.dumps(bad), json.dumps(good()))
    d = await drafting.draft_agent("Triage GitHub issues", grader, examples=["Triage #1"], constraints="No network")
    assert d["slug"] == "issue-triager" and len(grader.calls) == 2
    first, second = (c["messages"] for c in grader.calls)
    assert all(m.role == "user" for m in first + second) and len(second) == 1
    assert "<example_task" in first[0].text and "<constraints>" in first[0].text
    assert "<problems>" in second[0].text and "slug" in second[0].text
    assert grader.calls[0]["system"] == drafting.DRAFT_SYSTEM


async def test_draft_fails_after_second_invalid_reply():
    """AUTH3: two invalid replies raise DraftError with the validation errors; nothing else is attempted."""
    grader = ScriptedProvider("not json", json.dumps({**good(), "tools": ["teleport"]}))
    with pytest.raises(drafting.DraftError) as exc:
        await drafting.draft_agent("x", grader)
    assert any("unknown tools" in e for e in exc.value.errors)
    with pytest.raises(ValueError):
        await drafting.draft_agent("   ", ScriptedProvider())


async def test_fake_grader_returns_fixed_draft_for_drafting_prompt_only():
    """AUTH4: fake:grader returns FAKE_DRAFT for the drafting prompt and still judges otherwise."""
    fake = make_provider("fake:grader")
    assert (await drafting.draft_agent("anything", fake))["name"] == FAKE_DRAFT["name"]
    judged = await fake.complete("You are a strict grader", [Message(role="user", text="x")], [])
    assert json.loads(judged.text)["passed"] is True


async def test_anthropic_draft_uses_structured_output_without_temperature():
    """AUTH1: Claude is called with output_config.format json_schema, no temperature, user-only messages."""
    seen: list[dict] = []

    class Messages:
        async def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text",
                                                                                     text=json.dumps(good()))])

    provider = AnthropicProvider("claude-opus-5-5", client=SimpleNamespace(messages=Messages()))
    d = await drafting.draft_agent("Triage issues", provider)
    assert d["network"] is True
    [kw] = seen
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"] is drafting.DRAFT_SCHEMA
    assert "temperature" not in kw and [m["role"] for m in kw["messages"]] == ["user"]


def _no_unsupported(schema) -> None:
    if isinstance(schema, dict):
        assert not {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"} & set(schema)
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert set(schema["required"]) == set(schema["properties"])
        for v in schema.values():
            _no_unsupported(v)
    elif isinstance(schema, list):
        for v in schema:
            _no_unsupported(v)


def test_draft_schema_uses_only_supported_constraints():
    """AUTH1: the structured output schema avoids constraints the API does not support."""
    _no_unsupported(drafting.DRAFT_SCHEMA)


def test_draft_prompt_states_the_key_instructions():
    """AUTH5: the drafting prompt asks for a specific prompt structure and concrete pass/fail rubrics."""
    p = drafting.DRAFT_SYSTEM
    for phrase in ("Role and goal", "Working method", "Output", "Avoid", "pass/fail", "two thirds",
                   "shell\" only when"):
        assert phrase in p


# ---------------------------------------------------------------- PRUNE: decisions


def test_classify_counts_ties_as_unhelpful():
    """PRUNE2: removing a lesson that keeps train passes equal (or higher) marks it unhelpful."""
    assert pruning.classify(3, 3) == "unhelpful"
    assert pruning.classify(3, 4) == "unhelpful"
    assert pruning.classify(3, 2) == "helpful"
    assert pruning.classify(0, 0) == "unhelpful"
    assert pruning.classify(3, 3, complete=False) == "unknown"


def test_select_for_ablation_oldest_first_and_capped():
    """PRUNE3: lessons are ablated oldest first (by created_in_version, then position) up to the cap."""
    lessons = ["a", "b", "c", "d"]
    meta = {"a": {"created_in_version": 5}, "b": {"created_in_version": 2}, "d": {"created_in_version": 2}}
    assert pruning.select_for_ablation(lessons, meta, 3) == ["c", "b", "d"]
    assert pruning.select_for_ablation(lessons, None, 8) == lessons
    assert pruning.select_for_ablation(lessons * 3, None) == (lessons * 3)[:8]


def test_train_passes_reports_incomplete():
    train = [{"id": "c1"}, {"id": "c2"}]
    assert pruning.train_passes({"c1": {"passed": True}, "c2": {"passed": False}}, train) == (1, 2, True)
    assert pruning.train_passes({"c1": {"passed": True}}, train) == (1, 2, False)


CASES = [{"id": "t1", "split": "train"}, {"id": "t2", "split": "train"}, {"id": "h1", "split": "holdout"}]


def r(*passed: bool) -> dict:
    return {cid: {"passed": p, "reason": "", "run_id": f"r_{cid}"} for cid, p in zip(("t1", "t2", "h1"), passed)}


def test_is_eligible_prune_accepts_ties_and_rejects_regressions():
    """PRUNE4: equal passes with zero regressions is eligible (unlike is_eligible); any regression blocks."""
    from forge.improve import is_eligible

    assert pruning.is_eligible_prune(CASES, r(True, True, True), r(True, True, True))
    assert not is_eligible(CASES, r(True, True, True), r(True, True, True))
    assert pruning.is_eligible_prune(CASES, r(True, False, False), r(True, True, False))
    assert not pruning.is_eligible_prune(CASES, r(True, True, True), r(True, True, False))  # holdout regression
    assert not pruning.is_eligible_prune(CASES, r(True, False, True), r(False, True, True))  # swap is a regression


def test_removal_evidence_text():
    assert pruning.removal_evidence(4, 4, 4) == "removing this lesson kept train at 4/4"
    assert "dropped train from 2/2 to 0/2" in pruning.kept_evidence("helpful", 2, 0, 2)


# ---------------------------------------------------------------- PRUNE: provenance


def test_apply_lesson_edits_meta_records_provenance():
    """PRUNE5: added and edited lessons get created_in_version and evidence; kept lessons keep their entry;
    removed lessons drop out of the metadata."""
    old = ["keep me", "edit me", "drop me"]
    meta = {"keep me": {"created_in_version": 2, "evidence_run_ids": ["r_a"], "origin": "optimizer"},
            "drop me": {"created_in_version": 3}}
    edits = [LessonEdit(op="add", text="new one", evidence_run_ids=["r_b", "r_c"]),
             LessonEdit(op="edit", old_text="edit me", text="edited", evidence_run_ids=["r_d"]),
             LessonEdit(op="remove", text="drop me")]
    lessons, out = apply_lesson_edits_meta(old, meta, edits, 7, at=100.0)
    assert lessons == ["keep me", "edited", "new one"]
    assert out["keep me"] == meta["keep me"]
    assert out["new one"] == {"created_in_version": 7, "evidence_run_ids": ["r_b", "r_c"], "origin": "optimizer",
                              "op": "add", "created_at": 100.0}
    assert out["edited"]["edited_from"] == "edit me" and out["edited"]["evidence_run_ids"] == ["r_d"]
    assert "drop me" not in out and set(out) == set(lessons)


# ---------------------------------------------------------------- HTTP (no Docker)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_GRADER_MODEL", "fake:grader")
    monkeypatch.delenv("FORGE_TRUST_ALL", raising=False)
    from forge.server.app import app

    with TestClient(app, client=LOCAL) as c:
        yield c


def test_draft_endpoint_returns_draft_and_creates_nothing(client):
    """AUTH6: POST /agent-drafts returns the draft with a free slug and creates no agent; remote is 401."""
    d = client.post("/v1/agent-drafts", json={"description": "Triage our GitHub issues"}).json()
    assert d["slug"] == "issue-triager" and len(d["eval_cases"]) == 4
    assert client.get("/v1/agents").json() == []
    assert client.post("/v1/agent-drafts", json={"description": " "}).status_code == 400
    body = {k: d[k] for k in ("slug", "name", "description", "system_prompt", "tools", "network")}
    r = client.post("/v1/agents", json={**body, "model": "fake:echo", "eval_cases": d["eval_cases"][:2]})
    assert r.status_code == 200, r.text
    assert len(client.get(f"/v1/agents/{d['slug']}/evals").json()) == 2
    assert client.post("/v1/agent-drafts", json={"description": "again"}).json()["slug"] == "issue-triager-2"
    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.post("/v1/agent-drafts", json={"description": "x"}).status_code == 401


def test_prune_requires_two_lessons_and_a_train_case(client):
    """PRUNE1: pruning is refused with 400 before any job when prod has fewer than 2 lessons or no train cases."""
    base = {"slug": "p", "name": "p", "system_prompt": "x", "model": "fake:echo"}
    client.post("/v1/agents", json={**base, "lessons": ["only one"]})
    r = client.post("/v1/agents/p/prune", json={})
    assert r.status_code == 400 and "at least 2 lessons" in r.json()["detail"]
    client.post("/v1/agents", json={**base, "slug": "q", "lessons": ["one", "two"]})
    assert "train" in client.post("/v1/agents/q/prune", json={}).json()["detail"]
    assert client.get("/v1/agents/q/improvements").json() == []
    v1 = client.get("/v1/agents/q/versions/1").json()
    assert v1["lesson_meta"]["one"]["origin"] == "human" and v1["lesson_meta"]["one"]["created_in_version"] == 1
