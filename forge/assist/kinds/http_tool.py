"""Assist kind "http_tool": an HTTP tool definition from a description, a curl command or an API doc snippet.

Input: {"source": str (required), "existing": [str] (tool names already in the draft)}. Result (after the server
converts the model's fields): {"name", "description", "method", "url", "parameters": object (JSON schema),
"headers": {name: value}, "query_params": [str]}. Credentials are always secret references `{{NAME}}`; a literal
credential from the source never reaches the result.
"""

from __future__ import annotations

import json
import re
from typing import Any

from forge.assist import context as C
from forge.assist.registry import AssistRequest, Prepared, register

KIND = "http_tool"
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
AUTH_HEADER_RE = re.compile(r"auth|token|key|secret|cookie|session|signature|password", re.I)
SECRET_VALUE_RE = re.compile(r"^(?:(?:Bearer|Token|Basic|Bot)\s+)?\{\{[A-Z][A-Z0-9_]*\}\}$")
SECRET_REF_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
QUERY_SECRET_RE = re.compile(r"[?&](?:api[_-]?key|key|token|access_token|secret|password)=([^&{]+)", re.I)

SYSTEM = """You turn an API description, a curl command or an API doc snippet into a typed HTTP tool that an \
AI agent can call. Forge calls the endpoint from its server with the arguments the agent provides.

Tool definition:
- name: snake_case verb phrase, 3 to 40 characters, starting with a letter (e.g. get_weather, create_issue). \
Not one of the existing tool names and not bash, read_file, write_file or edit_file.
- description: one or two sentences telling the agent what the endpoint does, when to use it and what it \
returns.
- method: GET, POST, PUT, PATCH or DELETE.
- url: the full https URL template. Path parameters are written {param} and must be properties of the \
parameters schema. Never put credentials in the URL.
- parameters_json: a JSON schema (as a JSON string) with "type": "object", "properties" (each with a type and a \
description) and "required". Arguments not used in the URL go to the query string for GET and DELETE and to a \
JSON body otherwise.
- headers: list of {"name", "value"}. Credentials must be secret references like "Bearer {{API_TOKEN}}" or \
"{{SERVICE_API_KEY}}" (UPPER_SNAKE_CASE; reuse a configured secret name when one fits). NEVER copy a literal \
token, key or password from the source; replace it with a reference. Leave out headers Forge sets itself \
(Content-Type, Accept, User-Agent).
- query_params: names of arguments that must always go to the query string (usually empty).

Respond with the JSON object only: {"result": {"name": "...", "description": "...", "method": "GET", \
"url": "...", "parameters_json": "{...}", "headers": [{"name": "...", "value": "..."}], "query_params": []}, \
"rationale": "..."}"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"}, "description": {"type": "string"},
        "method": {"type": "string", "enum": METHODS}, "url": {"type": "string"},
        "parameters_json": {"type": "string"},
        "headers": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"},
                                                                                  "value": {"type": "string"}},
                                               "required": ["name", "value"], "additionalProperties": False}},
        "query_params": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "description", "method", "url", "parameters_json", "headers", "query_params"],
    "additionalProperties": False,
}


def literal_secrets(source: str) -> list[str]:
    """Credential looking literals in the source (Authorization and key headers, key query params, -u user:pass),
    which must not reach the result."""
    found = []
    for m in re.finditer(r"(?i)(?:authorization|x-api-key|api-key|token|apikey)\s*:\s*(?:bearer\s+|token\s+|basic\s+)?"
                         r"([^\s'\"\\]+)", source):
        found.append(m.group(1))
    found += [m.group(1) for m in QUERY_SECRET_RE.finditer(source)]
    found += [m.group(1) for m in re.finditer(r"(?:-u|--user)\s+['\"]?([^\s'\"]+)", source)]
    return [f for f in found if len(f) >= 6 and not SECRET_REF_RE.search(f) and not f.startswith("$")]


def build_context(req: AssistRequest) -> Prepared:
    assert req.version is not None
    source = C.text_input(req.input, "source", 8000, required=True)
    existing = sorted({t for t in req.input.get("existing") or [] if isinstance(t, str)} |
                      {h.get("name") for h in req.version.get("http_tools") or [] if isinstance(h, dict)})
    ctx, rendered = C.for_request(req)
    secrets = literal_secrets(source)
    shown = source
    for s in secrets:
        shown = shown.replace(s, "[REDACTED]")  # the model never sees the literal credential either
    parts = [rendered, C.bullets("existing_tool_names", existing) if existing else "",
             f"<source>\n{shown}\n</source>", "Write the HTTP tool as the JSON object."]
    return Prepared(text="\n\n".join(p for p in parts if p),
                    data={"existing": existing, "secrets": secrets},
                    diagnostics={"source_chars": len(source), "redacted_literals": len(secrets),
                                 "existing_tools": len(existing), "files_listed": len(ctx["files"])})


def _to_tool(result: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        params = json.loads(result.get("parameters_json") or "")
    except (TypeError, ValueError) as exc:
        return None, f"parameters_json is not valid JSON: {exc}"
    headers = {str(h.get("name") or "").strip(): str(h.get("value") or "")
               for h in result.get("headers") or [] if isinstance(h, dict)}
    return {"name": result.get("name"), "description": result.get("description"), "method": result.get("method"),
            "url": result.get("url"), "parameters": params, "headers": headers,
            "query_params": [q for q in result.get("query_params") or [] if isinstance(q, str)]}, None


def validate(result: dict[str, Any], data: dict[str, Any]) -> list[str]:
    from forge.tools import validate_tool_config
    from forge.types import HttpTool

    tool, err = _to_tool(result)
    if tool is None:
        return [err or "invalid tool"]
    errs: list[str] = []
    if not str(tool["description"] or "").strip():
        errs.append("description is empty")
    if tool["name"] in data.get("existing", []):
        errs.append(f"a tool named {tool['name']!r} already exists; pick another name")
    try:
        model = HttpTool(**tool)
    except Exception as exc:  # noqa: BLE001  pydantic validation error
        return errs + [f"invalid tool: {C.clip(str(exc), 300)}"]
    errs += validate_tool_config([model], [], [], None)
    if SECRET_REF_RE.search(tool["url"] or "") or QUERY_SECRET_RE.search(tool["url"] or ""):
        errs.append("the URL must not carry credentials; send them in a header with a {{SECRET}} reference")
    for name, value in tool["headers"].items():
        if not name:
            errs.append("a header has no name")
        elif AUTH_HEADER_RE.search(name) and not SECRET_VALUE_RE.match(value.strip()):
            errs.append(f"header {name!r} must be a secret reference such as 'Bearer {{{{API_TOKEN}}}}', not a literal")
    blob = json.dumps(result)
    for s in data.get("secrets") or []:
        if s in blob:
            errs.append("the tool copies a literal credential from the source; use a {{SECRET}} reference")
            break
    for q in tool["query_params"]:
        if q not in (tool["parameters"].get("properties") or {}):
            errs.append(f"query param {q!r} is not a parameter")
    return errs


def finalize(result: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    tool, _ = _to_tool(result)
    return tool or result


def fake(user: str) -> dict[str, Any]:
    m = re.search(r"<source>\n(.*?)\n</source>", user, re.S)
    source = m.group(1) if m else ""
    url_m = re.search(r"https?://[^\s'\"]+", source)
    url = url_m.group(0).split("?")[0] if url_m else "https://api.example.com/items/{item_id}"
    method_m = re.search(r"(?:-X\s+|\b)(GET|POST|PUT|PATCH|DELETE)\b", source)
    method = method_m.group(1) if method_m else "GET"
    params = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", url)
    last = [p for p in url.rstrip("/").split("/") if p and "{" not in p and "." not in p]
    noun = re.sub(r"[^a-z0-9]+", "_", (last[-1] if last else "item").lower()).strip("_") or "item"
    name = f"{method.lower()}_{noun}"[:40]
    existing = re.search(r"<existing_tool_names>\n(.*?)\n</existing_tool_names>", user, re.S)
    if existing and f"- {name}" in existing.group(1):
        name = f"{name}_2"
    schema = {"type": "object", "properties": {p: {"type": "string", "description": f"The {p}."} for p in params},
              "required": params}
    headers = [{"name": "Authorization", "value": "Bearer {{API_TOKEN}}"}] if re.search(r"(?i)authorization|token",
                                                                                        source) else []
    return {"result": {"name": name, "description": f"Calls {method} {url} and returns the JSON response.",
                       "method": method, "url": url, "parameters_json": json.dumps(schema), "headers": headers,
                       "query_params": []},
            "rationale": "Built from the given source."}


SPEC = register(KIND, build_context, SYSTEM, SCHEMA, validate, share_allowed=False, needs_agent=True, fake=fake,
                finalize=finalize, result_text=lambda r: str(r.get("name") or ""))
