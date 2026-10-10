"""Live Claude tests (needs ANTHROPIC_API_KEY). Models: FORGE_LIVE_MODEL (default: the server's default agent
model) for the agent and FORGE_LIVE_GRADER_MODEL (default anthropic:claude-opus-5-5) for the judge."""

from __future__ import annotations

import os

import pytest

from forge import improve
from forge.providers import make_provider
from forge.server.evals import DEFAULT_MODEL
from tests.live.conftest import assert_hello_done, env, run_hello_task

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]

AGENT_MODEL = env("FORGE_LIVE_MODEL", DEFAULT_MODEL)
GRADER_MODEL = env("FORGE_LIVE_GRADER_MODEL", "anthropic:claude-opus-5-5")


async def test_claude_agent_creates_and_runs_hello_py_in_sandbox():
    """FR2, FR12, NFR1: a real Claude agent in a Docker sandbox writes hello.py, runs it, and the diff shows it."""
    result, evs, diff = await run_hello_task(AGENT_MODEL)
    assert_hello_done(result, evs, diff)
    assert result.tokens_in > 0 and result.tokens_out > 0
    if result.turns > 1:  # FR12: later turns read the cached tools + system + conversation prefix
        assert result.cache_read_tokens > 0, result


async def test_claude_judge_passes_good_work_and_fails_bad_work():
    """FR9, FR10: the LLM judge (called exactly as eval runs call it) passes an obviously correct result and
    fails an obviously wrong one."""
    grader = make_provider(GRADER_MODEL)
    rubric = "The workspace contains hello.py and running it prints exactly 'hi'."
    good_diff = "diff --git a/hello.py b/hello.py\nnew file mode 100644\n--- /dev/null\n+++ b/hello.py\n" \
                "@@ -0,0 +1 @@\n+print('hi')\n"
    ok, reason = await improve.judge(rubric, "Create hello.py that prints hi.",
                                     "Created hello.py; `python hello.py` printed hi.", good_diff, grader)
    assert ok is True, reason
    ok, reason = await improve.judge(rubric, "Create hello.py that prints hi.",
                                     "I could not finish the task.", "", grader)
    assert ok is False, reason
