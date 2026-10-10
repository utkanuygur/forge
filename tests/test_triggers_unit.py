"""Layer 1: trigger building blocks. Cron parsing and next-fire times (incl. DST), HMAC verification for GitHub
and Slack (incl. the replay window), template rendering, Slack mrkdwn, the rate limiter and GitHub event filters."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from forge.triggers import github, text
from forge.triggers.cron import Cron, CronError
from forge.triggers.security import (RateLimiter, github_signature, slack_signature, verify_github, verify_slack)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def ts(*a: int, tz=UTC) -> float:
    return datetime(*a, tzinfo=tz).timestamp()


def utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC).replace(tzinfo=None)


# ---------------------------------------------------------------- cron parsing


@pytest.mark.parametrize("expr,field,expected", [
    ("*/15 * * * *", "minutes", {0, 15, 30, 45}),
    ("10-30/10 * * * *", "minutes", {10, 20, 30}),
    ("5/20 * * * *", "minutes", {5, 25, 45}),
    ("1,2,40-42 * * * *", "minutes", {1, 2, 40, 41, 42}),
    ("0 9-17/4 * * *", "hours", {9, 13, 17}),
    ("0 0 * jan,JUL *", "months", {1, 7}),
    ("0 0 * * mon-fri", "weekdays", {1, 2, 3, 4, 5}),
    ("0 0 * * 7", "weekdays", {0}),
    ("0 0 * * 5-7", "weekdays", {5, 6, 0}),
    ("@hourly", "minutes", {0}),
])
def test_cron_parses_fields(expr, field, expected):
    """TRG1: numbers, names, ranges, steps, lists, macros; 7 means Sunday."""
    assert set(getattr(Cron.parse(expr), field)) == expected


@pytest.mark.parametrize("expr", ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
                                  "* * * 13 *", "* * * * 8", "*/0 * * * *", "5-1 * * * *", "abc * * * *",
                                  "1,,2 * * * *", "0 0 30 2 *", "0 0 31 4 *", "@often"])
def test_cron_rejects_invalid_expressions(expr):
    """TRG1: wrong field counts, out-of-range values, bad steps and ranges, and never-firing dates are errors."""
    with pytest.raises(CronError):
        Cron.parse(expr)


def test_cron_next_is_strictly_after():
    """TRG1: next_after returns the first slot strictly after the given time."""
    c = Cron.parse("*/15 * * * *")
    assert utc(c.next_after(ts(2026, 1, 1, 0, 0, 30))) == datetime(2026, 1, 1, 0, 15)
    assert utc(c.next_after(ts(2026, 1, 1, 0, 15))) == datetime(2026, 1, 1, 0, 30)
    assert utc(c.next_after(ts(2026, 12, 31, 23, 59))) == datetime(2027, 1, 1, 0, 0)


def test_cron_weekdays_skip_the_weekend():
    """TRG1: "every weekday at 9:00" from Friday morning is Monday 9:00."""
    c = Cron.parse("0 9 * * 1-5")
    assert utc(c.next_after(ts(2026, 10, 9, 10))) == datetime(2026, 10, 12, 9)
    assert [utc(x).day for x in c.next_n(ts(2026, 10, 9, 8), 3)] == [9, 12, 13]


def test_cron_day_of_month_or_day_of_week():
    """TRG1: when both day fields are restricted, either one matching is enough (Vixie cron)."""
    c = Cron.parse("0 0 13 * 5")
    first = c.next_after(ts(2026, 10, 10))
    assert utc(first) == datetime(2026, 10, 13)          # the 13th, a Tuesday
    assert utc(c.next_after(first)) == datetime(2026, 10, 16)  # next Friday


def test_cron_month_lengths_and_leap_years():
    """TRG1: day 31 skips 30-day months; Feb 29 waits for the next leap year."""
    assert utc(Cron.parse("0 0 31 * *").next_after(ts(2026, 9, 1))) == datetime(2026, 10, 31)
    assert utc(Cron.parse("0 0 29 2 *").next_after(ts(2026, 3, 1))) == datetime(2028, 2, 29)


def test_cron_timezone_offsets():
    """TRG1: 9:00 in Berlin is 07:00 UTC in summer and 08:00 UTC after DST ends (2026-10-25)."""
    c = Cron.parse("0 9 * * *")
    assert utc(c.next_after(ts(2026, 10, 24), "Europe/Berlin")) == datetime(2026, 10, 24, 7)
    assert utc(c.next_after(ts(2026, 10, 25), "Europe/Berlin")) == datetime(2026, 10, 25, 8)


def test_cron_dst_spring_forward_shifts_nonexistent_time():
    """TRG1: on 2026-03-08 New York skips 02:00 to 03:00; a 02:30 job runs at 03:30 EDT that day, then 02:30."""
    c = Cron.parse("30 2 * * *")
    first = c.next_after(ts(2026, 3, 7, 12, tz=NY), NY)
    assert datetime.fromtimestamp(first, NY).strftime("%m-%d %H:%M %Z") == "03-08 03:30 EDT"
    assert datetime.fromtimestamp(c.next_after(first, NY), NY).strftime("%m-%d %H:%M %Z") == "03-09 02:30 EDT"
    # every minute keeps ticking across the gap: 01:59 EST is followed by 03:00 EDT one real minute later
    every = Cron.parse("* * * * *")
    t = ts(2026, 3, 8, 6, 59)  # 01:59 EST
    assert every.next_after(t, NY) == t + 60


def test_cron_dst_fall_back_fixed_time_fires_once():
    """TRG1: on 2026-11-01 01:30 happens twice in New York; a fixed-time job fires once, on the first."""
    c = Cron.parse("30 1 * * *")
    first = c.next_after(ts(2026, 10, 31, 12, tz=NY), NY)
    assert utc(first) == datetime(2026, 11, 1, 5, 30)  # 01:30 EDT
    assert utc(c.next_after(first, NY)) == datetime(2026, 11, 2, 6, 30)  # 01:30 EST next day


def test_cron_dst_fall_back_hourly_fires_in_both_hours():
    """TRG1: an hour-wildcard job is interval-like and fires in both occurrences of the repeated hour."""
    c = Cron.parse("30 * * * *")
    times = c.next_n(ts(2026, 11, 1, 4, 45), 3, NY)  # 00:45 EDT
    assert [utc(x) for x in times] == [datetime(2026, 11, 1, 5, 30), datetime(2026, 11, 1, 6, 30),
                                       datetime(2026, 11, 1, 7, 30)]


def test_cron_unknown_timezone():
    with pytest.raises(CronError):
        Cron.parse("* * * * *").next_after(0, "Mars/Olympus")


# ---------------------------------------------------------------- signatures


def test_github_signature_verification():
    """TRG2: X-Hub-Signature-256 is HMAC-SHA256 of the raw body; wrong secret, body, format or none fail."""
    body = b'{"zen": "hi"}'
    sig = github_signature("s3cret", body)
    assert sig.startswith("sha256=") and verify_github("s3cret", body, sig)
    assert not verify_github("other", body, sig)
    assert not verify_github("s3cret", body + b" ", sig)
    assert not verify_github("s3cret", body, sig.removeprefix("sha256="))
    assert not verify_github("s3cret", body, None)
    assert not verify_github(None, body, sig)
    # GitHub documents this exact test vector
    assert github_signature("It's a Secret to Everybody", b"Hello, World!") == \
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"


def test_slack_signature_verification_and_replay_window():
    """TRG3: v0 signature over `v0:ts:body`; timestamps more than 5 minutes off are rejected even if signed."""
    body = b"command=%2Fforge&text=hi"
    now = 1_760_000_000
    stamp = str(now)
    sig = slack_signature("sign", stamp, body)
    assert verify_slack("sign", body, stamp, sig, now=now) == (True, "")
    assert verify_slack("sign", body, stamp, sig, now=now + 299)[0]
    assert verify_slack("sign", body, stamp, sig, now=now + 301) == (False, "Slack request timestamp is too old")
    assert not verify_slack("sign", body, stamp, sig, now=now - 301)[0]
    old = str(now - 600)
    assert not verify_slack("sign", body, old, slack_signature("sign", old, body), now=now)[0]
    assert not verify_slack("wrong", body, stamp, sig, now=now)[0]
    assert not verify_slack("sign", body + b"x", stamp, sig, now=now)[0]
    assert not verify_slack("sign", body, None, sig, now=now)[0]
    assert not verify_slack("sign", body, "abc", sig, now=now)[0]
    assert not verify_slack(None, body, stamp, sig, now=now)[0]


# ---------------------------------------------------------------- templates and formatting


def test_template_rendering_is_safe():
    """TRG4: known fields are substituted, unknown placeholders stay, braces escape, no attribute access."""
    f = {"repo": "a/b", "title": "Fix {it}", "n": 3}
    assert text.render("{repo}#{n}: {title}", f) == "a/b#3: Fix {it}"
    assert text.render("{missing} {repo}", f) == "{missing} a/b"
    assert text.render("{{repo}} {repo.__class__} {0}", f) == "{repo} {repo.__class__} {0}"
    assert text.render("a\n\n\n\nb {none}", {"none": None}) == "a\n\nb"
    assert len(text.render("{x}", {"x": "y" * 50_000})) == text.MAX_FIELD


def test_markdown_to_slack_mrkdwn():
    """TRG3: bold, italics, strike, links, headings and bullets convert; code is untouched; <, >, & escape."""
    md = "# Title\n**bold** and *it* and ~~gone~~\n- item [docs](https://x.dev/a)\n`a**b**`\n```\nif a < b: **x**\n```"
    out = text.to_mrkdwn(md)
    assert out.splitlines() == ["*Title*", "*bold* and _it_ and ~gone~", "• item <https://x.dev/a|docs>",
                                "`a**b**`", "```", "if a &lt; b: **x**", "```"]
    assert text.to_mrkdwn("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"


def test_truncation_keeps_messages_within_limits():
    """TRG3: long output is cut at a line break, open code fences are closed and the run link survives."""
    long = "```\n" + "\n".join(f"line {i}" for i in range(2000))
    msg = text.slack_message(long, "succeeded", "http://h/runs/r_1")
    assert len(msg) <= text.SLACK_TEXT_LIMIT
    assert msg.endswith("<http://h/runs/r_1|View the run>") and "Output truncated." in msg
    body = msg.split("\n...")[0]
    assert body.count("```") % 2 == 0
    assert text.slack_message("ok", "failed", "u").startswith("*Run failed.*")
    gh = text.github_comment("x" * 70_000, "succeeded", "http://h/runs/r_1", "r_1")
    assert len(gh) <= text.GITHUB_COMMENT_LIMIT and gh.endswith("[r_1](http://h/runs/r_1)</sub>")


# ---------------------------------------------------------------- rate limiter


def test_rate_limiter_sliding_window():
    """TRG5: at most N per hour per key; the window slides; keys are independent."""
    clock = [1000.0]
    rl = RateLimiter(clock=lambda: clock[0])
    assert [rl.allow("a", 3) for _ in range(4)] == [True, True, True, False]
    assert rl.allow("b", 3)
    assert 0 < rl.retry_after("a") <= 3601
    clock[0] += 3599
    assert not rl.allow("a", 3)
    clock[0] += 2
    assert rl.allow("a", 3)
    rl.reset("a")
    assert rl.allow("a", 1) and not rl.allow("a", 1)


# ---------------------------------------------------------------- GitHub event filters

CFG = {"events": ["issues", "issue_comment", "pull_request", "push"], "label": "", "command": "/forge",
       "branches": ["main", "release/*"], "pr_actions": ["opened", "synchronize"]}
REPO = {"full_name": "o/r"}
USER = {"login": "alice", "type": "User"}


def test_github_issue_and_label_filters():
    """TRG2: opened issues fire without a label filter; with one, only `labeled` with that label fires."""
    issue = {"number": 7, "title": "Bug", "body": "Broken", "html_url": "https://g/o/r/issues/7", "user": USER}
    f, _ = github.parse("issues", {"action": "opened", "issue": issue, "repository": REPO, "sender": USER}, CFG)
    assert f and f["number"] == 7 and f["repo"] == "o/r" and f["sender"] == "alice"
    assert "Bug" in github.build_input(CFG, f) and "Broken" in github.build_input(CFG, f)
    labeled = {**CFG, "label": "forge"}
    assert github.parse("issues", {"action": "opened", "issue": issue, "repository": REPO, "sender": USER},
                        labeled)[0] is None
    assert github.parse("issues", {"action": "labeled", "label": {"name": "bug"}, "issue": issue, "sender": USER},
                        labeled)[0] is None
    assert github.parse("issues", {"action": "labeled", "label": {"name": "Forge"}, "issue": issue, "sender": USER},
                        labeled)[0]
    assert github.parse("issues", {"action": "closed", "issue": issue, "sender": USER}, CFG)[0] is None


def test_github_comment_command_filter():
    """TRG2: only new comments that start with the command fire; the rest of the comment becomes {comment}."""
    def p(body, sender=USER, action="created"):
        return {"action": action, "comment": {"body": body, "html_url": "u"}, "sender": sender, "repository": REPO,
                "issue": {"number": 3, "title": "T", "pull_request": {"html_url": "https://g/pr/3"}}}
    f, _ = github.parse("issue_comment", p("/forge  fix the tests"), CFG)
    assert f and f["comment"] == "fix the tests" and f["pr_url"] == "https://g/pr/3"
    assert github.parse("issue_comment", p("/FORGE go"), CFG)[0]
    for bad in ("please /forge go", "/forgery go"):
        assert github.parse("issue_comment", p(bad), CFG)[0] is None
    assert github.parse("issue_comment", p("/forge go", action="edited"), CFG)[0] is None
    assert github.parse("issue_comment", p("/forge go", sender={"login": "bot", "type": "Bot"}), CFG)[0] is None


def test_github_pull_request_and_push_filters():
    """TRG2: PR opened and synchronize fire; push fires only for matching branches and not for deletions."""
    pr = {"number": 9, "title": "Add", "html_url": "https://g/pull/9", "head": {"ref": "feat"}, "base": {"ref": "main"}}
    assert github.parse("pull_request", {"action": "synchronize", "pull_request": pr, "sender": USER}, CFG)[0]
    assert github.parse("pull_request", {"action": "closed", "pull_request": pr, "sender": USER}, CFG)[0] is None
    push = {"ref": "refs/heads/main", "commits": [{"message": "one\nmore"}, {"message": "two"}], "sender": USER,
            "repository": REPO, "compare": "https://g/compare"}
    f, _ = github.parse("push", push, CFG)
    assert f and f["branch"] == "main" and f["commits"] == "- one\n- two" and f["commit_count"] == 2
    assert github.parse("push", {**push, "ref": "refs/heads/release/1.0"}, CFG)[0]
    assert github.parse("push", {**push, "ref": "refs/heads/feature/x"}, CFG)[0] is None
    assert github.parse("push", {**push, "ref": "refs/tags/v1"}, CFG)[0] is None
    assert github.parse("push", {**push, "deleted": True}, CFG)[0] is None
    assert github.parse("push", push, {**CFG, "events": ["issues"]})[0] is None

