"""Convert DB rows into the JSON objects defined in CONTRACT.md."""

from __future__ import annotations

from pathlib import Path

from forge import db

VERSION_KEYS = ("id", "agent_id", "n", "system_prompt", "lessons", "model", "tools", "http_tools", "network",
                "seed_files", "parent_id", "created_by", "rationale", "created_at", "mcp_servers", "callable_agents")
LIST_DEFAULTS = ("mcp_servers", "callable_agents")


def version_view(v: dict) -> dict:
    return {k: (v.get(k) or []) if k in LIST_DEFAULTS else v[k] for k in VERSION_KEYS}


def agent_view(a: dict, *, versions: bool = False) -> dict:
    prod = db.get_version(a["prod_version_id"])
    out = {
        "id": a["id"], "slug": a["slug"], "name": a["name"], "description": a["description"],
        "prod_version": prod["n"] if prod else None, "auto_promote": a["auto_promote"],
        "share_token": a["share_token"], "created_at": a["created_at"],
    }
    if versions:
        out["versions"] = [version_view(v) for v in db.list_versions(a["id"])]
    return out


def run_view(r: dict, *, version_n: int | None = None) -> dict:
    if version_n is None:
        v = db.get_version(r["version_id"])
        version_n = v["n"] if v else None
    return {
        "id": r["id"], "agent_id": r["agent_id"], "version_id": r["version_id"], "version_n": version_n,
        "source": r["source"], "input": r["input"], "status": r["status"], "output": r["output"],
        "error": r["error"], "turns": r["turns"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
        "has_artifacts": bool(r["artifact_path"] and Path(r["artifact_path"]).exists()),
        "eval_case_id": r["eval_case_id"],
        "parent_run_id": r.get("parent_run_id"),
        "child_usage": {"tokens_in": r.get("child_tokens_in") or 0, "tokens_out": r.get("child_tokens_out") or 0,
                        "cost_usd": r.get("child_cost_usd") or 0},
        "created_at": r["created_at"], "started_at": r["started_at"], "ended_at": r["ended_at"],
        "feedback": [feedback_view(f) for f in db.list_feedback(r["id"])],
    }


def feedback_view(f: dict) -> dict:
    return {k: f[k] for k in ("id", "run_id", "rating", "comment", "source", "created_at")}


def event_view(e: dict) -> dict:
    return {k: e[k] for k in ("run_id", "seq", "ts", "type", "payload")}


def eval_case_view(c: dict) -> dict:
    return {k: c[k] for k in ("id", "agent_id", "name", "input", "seed_files", "checker_type", "checker", "split",
                              "created_at")}


def improvement_view(im: dict) -> dict:
    s = im["summary"] or {}
    base = db.get_version(im["base_version_id"])
    cand = db.get_version(im["candidate_version_id"])
    empty = {"train": {"passed": 0, "total": 0}, "holdout": {"passed": 0, "total": 0}}
    return {
        "id": im["id"], "agent_id": im["agent_id"], "status": im["status"],
        "base_version": base["n"] if base else None, "candidate_version": cand["n"] if cand else None,
        "base": s.get("base", empty), "candidate": s.get("candidate", empty), "cases": s.get("cases", []),
        "newly_passing": s.get("newly_passing", []), "regressions": s.get("regressions", []),
        "lessons_diff": s.get("lessons_diff", []), "error": s.get("error"),
        "created_at": im["created_at"], "ended_at": im["ended_at"],
    }
