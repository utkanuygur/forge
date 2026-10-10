"""GitHub webhook events: filtering, template fields and posting the result back as a comment."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from forge.triggers import text
from forge.triggers.core import post_json, run_url

DEFAULT_TEMPLATES = {
    "issues": "GitHub issue #{number} in {repo}, opened by {author}: {title}\n{url}\n\n{body}",
    "issue_comment": "{sender} asked on {repo}#{number} ({title}): {comment}\n{url}\n\nOriginal description:\n{body}",
    "pull_request": "Pull request #{number} in {repo} by {author} ({action}): {title}\n{pr_url}\n"
                    "Branch {branch} into {base}.\n\n{body}",
    "push": "{sender} pushed {commit_count} commit(s) to {branch} in {repo}.\n{compare}\n\n{commits}",
}

SAMPLE = {
    "event": "issues", "action": "opened", "repo": "octo-org/hello-world", "number": 1, "title": "Sample issue",
    "body": "This is a test event sent from Forge.", "comment": "", "url": "https://github.com/octo-org/hello-world/issues/1",
    "issue_url": "https://github.com/octo-org/hello-world/issues/1", "pr_url": "", "sender": "octocat",
    "author": "octocat", "label": "", "branch": "", "base": "", "ref": "", "commits": "", "commit_count": 0,
    "compare": "", "head_sha": "",
}


def _login(obj: Any) -> str:
    return (obj or {}).get("login") or ""


def _base_fields(event: str, p: dict) -> dict[str, Any]:
    return {k: "" for k in SAMPLE} | {
        "event": event, "action": p.get("action") or "", "repo": (p.get("repository") or {}).get("full_name") or "",
        "sender": _login(p.get("sender")), "commit_count": 0,
    }


def parse(event: str, p: dict, cfg: dict) -> tuple[dict[str, Any] | None, str]:
    """Returns (template fields, "") for an event this trigger should act on, else (None, reason ignored)."""
    if event not in (cfg.get("events") or []):
        return None, f"event {event!r} is not enabled for this trigger"
    sender = p.get("sender") or {}
    if sender.get("type") == "Bot" and event != "push":
        return None, "events from bots are ignored"
    f = _base_fields(event, p)
    if event in ("issues", "issue_comment"):
        issue = p.get("issue") or {}
        f.update(number=issue.get("number") or "", title=issue.get("title") or "", body=issue.get("body") or "",
                 url=issue.get("html_url") or "", issue_url=issue.get("html_url") or "",
                 author=_login(issue.get("user")))
        if issue.get("pull_request"):
            f["pr_url"] = (issue["pull_request"] or {}).get("html_url") or issue.get("html_url") or ""
    if event == "issues":
        label = (cfg.get("label") or "").strip()
        action = p.get("action")
        if label:
            name = ((p.get("label") or {}).get("name") or "")
            if action != "labeled" or name.lower() != label.lower():
                return None, f"waiting for the {label!r} label"
            f["label"] = name
        elif action != "opened":
            return None, f"issue action {action!r} is ignored"
        return f, ""
    if event == "issue_comment":
        if p.get("action") != "created":
            return None, "only new comments are handled"
        body = ((p.get("comment") or {}).get("body") or "").strip()
        command = (cfg.get("command") or "").strip()
        if not command or not body.lower().startswith(command.lower()):
            return None, f"comment does not start with {command!r}"
        rest = body[len(command):]
        if rest and not rest[0].isspace():
            return None, f"comment does not start with {command!r}"
        f["comment"] = rest.strip()
        f["url"] = (p.get("comment") or {}).get("html_url") or f["url"]
        return f, ""
    if event == "pull_request":
        action = p.get("action")
        if action not in (cfg.get("pr_actions") or ["opened", "synchronize"]):
            return None, f"pull request action {action!r} is ignored"
        pr = p.get("pull_request") or {}
        f.update(number=pr.get("number") or p.get("number") or "", title=pr.get("title") or "",
                 body=pr.get("body") or "", url=pr.get("html_url") or "", pr_url=pr.get("html_url") or "",
                 author=_login(pr.get("user")), branch=(pr.get("head") or {}).get("ref") or "",
                 base=(pr.get("base") or {}).get("ref") or "", head_sha=(pr.get("head") or {}).get("sha") or "")
        return f, ""
    if event == "push":
        ref = p.get("ref") or ""
        if not ref.startswith("refs/heads/"):
            return None, "only branch pushes are handled"
        if p.get("deleted"):
            return None, "branch deletions are ignored"
        branch = ref.removeprefix("refs/heads/")
        patterns = [b.strip() for b in cfg.get("branches") or [] if b.strip()]
        if patterns and not any(fnmatchcase(branch, pat) for pat in patterns):
            return None, f"branch {branch!r} does not match the branch filter"
        commits = p.get("commits") or []
        f.update(ref=ref, branch=branch, compare=p.get("compare") or "", url=p.get("compare") or "",
                 head_sha=(p.get("head_commit") or {}).get("id") or p.get("after") or "",
                 commit_count=len(commits), title=((p.get("head_commit") or {}).get("message") or "").split("\n")[0],
                 commits="\n".join("- " + (c.get("message") or "").split("\n")[0] for c in commits[:50]),
                 sender=_login(p.get("sender")) or (p.get("pusher") or {}).get("name") or "")
        return f, ""
    return None, f"event {event!r} is not supported"


def build_input(cfg: dict, fields: dict[str, Any]) -> str:
    template = (cfg.get("input_template") or "").strip() or DEFAULT_TEMPLATES.get(fields["event"], "{title}\n{body}")
    return text.render(template, fields)


def delivery_context(fields: dict[str, Any], cfg: dict, has_token: bool, base_url: str) -> dict | None:
    if not (cfg.get("comment_back", True) and has_token and fields.get("number") and fields.get("repo")):
        return None
    return {"deliver": "github_comment", "repo": fields["repo"], "number": fields["number"], "base_url": base_url}


async def deliver(t: dict, run: dict, ctx: dict) -> tuple[bool, str]:
    token = (t["tokens"] or {}).get("github_token")
    if not token:
        return False, "skipped"
    base = (t["config"].get("api_base") or "https://api.github.com").rstrip("/")
    url = f"{base}/repos/{ctx['repo']}/issues/{ctx['number']}/comments"
    body = {"body": text.github_comment(run["output"] or run.get("error") or "", run["status"],
                                        run_url(ctx.get("base_url"), run["id"]), run["id"])}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "forge-triggers"}
    return await post_json(url, body, headers)
