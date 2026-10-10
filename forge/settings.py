"""Settings registry: every Forge setting in one declarative list (see CONTRACT.md "Settings").

Each setting has a key, an environment variable, a type, a default, a group, a one sentence description and an
apply mode ("live": read at use time, so a change takes effect immediately; "restart": read once at server start).

Precedence: a non-empty environment variable (read only in the app) > a value saved in the `settings` table > the
default. Secrets (API keys) are only ever read from the environment; they are never stored in the database. The
desktop app keeps them in the Keychain and passes them to the server as environment variables, listing their names
in FORGE_DESKTOP_SECRETS so the app can say where they came from.

Other modules add their own settings with `register_setting(...)` at import time, e.g.

    from forge.settings import register_setting
    register_setting(key="share_rate_limit", env="FORGE_SHARE_RATE_LIMIT", type="int", default=30,
                     group="Sharing", label="Share link rate limit", description="Runs per minute per share link.",
                     min=1, max=1000)

and read them with `settings.get("share_rate_limit")` where they are used.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

log = logging.getLogger("forge.settings")

SettingType = Literal["string", "int", "float", "bool", "enum", "model", "url", "json", "secret"]
ApplyMode = Literal["live", "restart"]
Source = Literal["env", "saved", "default"]

GROUPS = ["Models", "Sandbox", "Evaluations", "Server", "Observability", "Pricing", "Sharing", "Tools", "Triggers"]

# Modules that register their own settings at import time (see the module docstring). load_all() imports them so
# the registry is complete wherever it is listed (.env.example, the Settings page), whatever was imported first.
SETTING_MODULES = ("forge.triggers.security", "forge.triggers.core", "forge.triggers.slack", "forge.server.assist",
                   "forge.tools.mcp", "forge.share_limits")

TYPES = {"string", "int", "float", "bool", "enum", "model", "url", "json", "secret"}
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


@dataclass
class Setting:
    key: str
    env: str
    type: SettingType
    default: Any
    group: str
    label: str
    description: str
    apply: ApplyMode = "live"
    secret: bool = False
    options: list[str] | None = None  # enum choices
    min: float | None = None  # int and float bounds (inclusive)
    max: float | None = None
    optional: bool = False  # model and url: the empty string is allowed (means "not set")
    validate: Callable[[Any], Any] | None = None  # extra check: returns the normalized value or raises ValueError
    editable: bool = True  # False: environment only, shown read only in the app
    widget: str | None = None  # UI hint for json settings: "endpoints" or "prices"
    placeholder: str = ""
    env_parse: Callable[[str], Any] | None = None  # custom parser for the environment string
    env_format: Callable[[Any], str] | None = None  # how .env.example writes the default

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"setting {self.key!r}: unknown type {self.type!r}")
        if self.type == "secret":
            self.secret = True
        if self.type == "enum" and not self.options:
            raise ValueError(f"setting {self.key!r}: enum needs options")


REGISTRY: dict[str, Setting] = {}


def register_setting(**kwargs: Any) -> Setting:
    """Adds a setting to the registry. Call it at import time of the module that uses the setting."""
    s = Setting(**kwargs)
    if s.key in REGISTRY:
        raise ValueError(f"setting {s.key!r} is already registered")
    if any(o.env == s.env for o in REGISTRY.values()):
        raise ValueError(f"environment variable {s.env} is already used by another setting")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", s.key):
        raise ValueError(f"setting key {s.key!r} must be lowercase snake case")
    s.default = coerce(s, s.default) if not s.secret and s.default is not None else s.default
    if s.group not in GROUPS:
        GROUPS.append(s.group)
    REGISTRY[s.key] = s
    return s


def setting(key: str) -> Setting:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown setting {key!r}") from None


# ---------------------------------------------------------------- validation


MODEL_RE = re.compile(r"^(anthropic:\S+|openai_compat:https?://\S+\|\S+|fake:[a-z]+)$")


def model_error(value: str) -> str | None:
    """Why `value` is not a valid model string, or None."""
    if MODEL_RE.match(value):
        return None
    return ("use anthropic:<model id>, openai_compat:<base url>|<model> or fake:<scenario>, "
            "for example anthropic:claude-sonnet-5-5")


def url_error(value: str) -> str | None:
    u = urlparse(value)
    if u.scheme not in ("http", "https") or not u.netloc:
        return "must be an http or https URL, for example https://forge.example.com"
    return None


def coerce(s: Setting, value: Any) -> Any:
    """The normalized value for setting `s`, or ValueError with a plain message."""
    t = s.type
    if t == "secret":
        raise ValueError("secrets are never stored by Forge; set them in the environment or the desktop app")
    if t in ("string", "model", "url", "enum"):
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("must be text")
        value = value.strip()
        if t == "enum" and value not in (s.options or []):
            raise ValueError(f"must be one of {', '.join(s.options or [])}")
        if t == "model" and not (s.optional and value == "") and (err := model_error(value)):
            raise ValueError(err)
        if t == "url" and not (s.optional and value == ""):
            if err := url_error(value):
                raise ValueError(err)
            value = value.rstrip("/")
        if t == "string" and not value and not s.optional:
            raise ValueError("must not be empty")
    elif t == "int":
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
            value = int(value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            raise ValueError("must be a whole number")
        _bounds(s, value)
    elif t == "float":
        if isinstance(value, bool):
            raise ValueError("must be a number")
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise ValueError("must be a number") from None
        if not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        value = float(value)
        _bounds(s, value)
    elif t == "bool":
        if isinstance(value, str) and value.strip().lower() in TRUE | FALSE:
            value = value.strip().lower() in TRUE
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
    elif t == "json":
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            raise ValueError("must be JSON") from None
    if s.validate is not None:
        value = s.validate(value)
    return value


def _bounds(s: Setting, value: float) -> None:
    if s.min is not None and value < s.min:
        raise ValueError(f"must be at least {_num(s.min)}")
    if s.max is not None and value > s.max:
        raise ValueError(f"must be at most {_num(s.max)}")


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def parse_env(s: Setting, raw: str) -> Any:
    """The typed value of an environment string, or ValueError."""
    if s.env_parse is not None:
        return coerce(s, s.env_parse(raw))
    if s.type == "json":
        try:
            return coerce(s, json.loads(raw))
        except json.JSONDecodeError:
            raise ValueError("is not valid JSON") from None
    return coerce(s, raw)


def validate_endpoints(value: Any) -> list[dict]:
    """OpenAI-compatible endpoints: [{name, base_url, api_key_env?}] with unique names and base URLs."""
    if not isinstance(value, list):
        raise ValueError("must be a list of endpoints")
    out, names, urls = [], set(), set()
    for i, e in enumerate(value):
        if not isinstance(e, dict):
            raise ValueError(f"endpoint {i + 1} must be an object with name and base_url")
        name = str(e.get("name") or "").strip()
        base = str(e.get("base_url") or "").strip().rstrip("/")
        key_env = str(e.get("api_key_env") or "").strip()
        if not name:
            raise ValueError(f"endpoint {i + 1} needs a name")
        if err := url_error(base):
            raise ValueError(f"endpoint {name!r}: base URL {err}")
        if key_env and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key_env):
            raise ValueError(f"endpoint {name!r}: the API key variable must look like MY_ENDPOINT_KEY")
        if name.lower() in names or base in urls:
            raise ValueError(f"endpoint {name!r} is listed twice")
        names.add(name.lower())
        urls.add(base)
        out.append({"name": name, "base_url": base, **({"api_key_env": key_env} if key_env else {})})
    return out


def validate_prices(value: Any) -> dict[str, list[float]]:
    """Model prices: {model: [input, output]} in USD per million tokens ({input, output} objects are accepted)."""
    if not isinstance(value, dict):
        raise ValueError("must be an object of model to [input, output]")
    out: dict[str, list[float]] = {}
    for k, v in value.items():
        model = str(k).strip()
        if not model:
            raise ValueError("a price needs a model")
        if isinstance(v, dict) and "input" in v and "output" in v:
            v = [v["input"], v["output"]]
        if not (isinstance(v, (list, tuple)) and len(v) == 2):
            raise ValueError(f"price for {model!r} must be [input, output]")
        try:
            pin, pout = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            raise ValueError(f"price for {model!r} must be two numbers") from None
        if pin < 0 or pout < 0:
            raise ValueError(f"price for {model!r} must not be negative")
        out[model] = [pin, pout]
    return out


def validate_memory(value: str) -> str:
    if not re.fullmatch(r"\d+(\.\d+)?[bkmg]?", value.lower()):
        raise ValueError("must be a size such as 512m or 2g")
    return value.lower()


def _log_json_env(raw: str) -> str:
    v = raw.strip().lower()
    return "on" if v in TRUE else "off" if v in FALSE else v


# ---------------------------------------------------------------- built in settings

OLLAMA_URL = "http://localhost:11434/v1"

register_setting(
    key="default_model", env="FORGE_DEFAULT_MODEL", type="model", default="anthropic:claude-sonnet-5-5",
    group="Models", label="Default agent model", description="Model given to new agents that do not pick one.")
register_setting(
    key="grader_model", env="FORGE_GRADER_MODEL", type="model", default="", optional=True,
    group="Models", label="Grader model",
    description="Model that judges evaluation runs and proposes improvements; empty means the default agent model.",
    placeholder="Same as the default agent model")
register_setting(
    key="openai_endpoints", env="FORGE_OPENAI_ENDPOINTS", type="json", widget="endpoints",
    default=[{"name": "Ollama", "base_url": OLLAMA_URL}], validate=validate_endpoints,
    group="Models", label="OpenAI compatible endpoints",
    description="Servers such as Ollama or vLLM whose models agents can use; a key variable names where its key lives.")
register_setting(
    key="anthropic_api_key", env="ANTHROPIC_API_KEY", type="secret", default=None,
    group="Models", label="Claude API key", description="Key for Claude models from console.anthropic.com.")
register_setting(
    key="openai_api_key", env="OPENAI_API_KEY", type="secret", default=None,
    group="Models", label="OpenAI compatible API key",
    description="Key sent to OpenAI compatible endpoints that do not name their own key variable.")

register_setting(
    key="executor", env="FORGE_EXECUTOR", type="enum", options=["docker", "local"], default="docker",
    group="Sandbox", label="Executor",
    description="Where agent tools run: docker isolates each run, local runs commands directly on this machine.")
register_setting(
    key="sandbox_image", env="FORGE_SANDBOX_IMAGE", type="string", default="forge-sandbox",
    group="Sandbox", label="Sandbox image", description="Docker image every run starts from.")
register_setting(
    key="sandbox_cpus", env="FORGE_SANDBOX_CPUS", type="float", default=1.0, min=0.1, max=64,
    group="Sandbox", label="Sandbox CPUs", description="CPU cores each sandbox container may use.")
register_setting(
    key="sandbox_memory", env="FORGE_SANDBOX_MEMORY", type="string", default="1g", validate=validate_memory,
    group="Sandbox", label="Sandbox memory", description="Memory limit of each sandbox container, such as 1g or 512m.")
register_setting(
    key="run_max_turns", env="FORGE_RUN_MAX_TURNS", type="int", default=30, min=1, max=500,
    group="Sandbox", label="Turn limit", description="Model turns a run may take before it stops with a timeout.")
register_setting(
    key="run_max_seconds", env="FORGE_RUN_MAX_SECONDS", type="int", default=600, min=10, max=86400,
    group="Sandbox", label="Time limit (seconds)",
    description="Wall clock seconds a run may take before it stops with a timeout.")

register_setting(
    key="eval_repeats", env="FORGE_EVAL_REPEATS", type="int", default=3, min=1, max=9,
    group="Evaluations", label="Default eval repeats",
    description="Runs per eval case for agents without their own setting; a case passes on a strict majority.")
register_setting(
    key="eval_concurrency", env="FORGE_EVAL_CONCURRENCY", type="int", default=4, min=1, max=64,
    group="Evaluations", label="Eval concurrency", description="Eval runs of one version that may run at the same time.")
register_setting(
    key="auto_improve_threshold", env="FORGE_AUTO_IMPROVE_THRESHOLD", type="int", default=3, min=1, max=1000,
    group="Evaluations", label="Auto improve threshold",
    description="New thumbs down that start an improvement cycle for agents with auto promote on.")

register_setting(
    key="max_concurrent_runs", env="FORGE_MAX_CONCURRENCY", type="int", default=8, min=1, max=256, apply="restart",
    group="Server", label="Max concurrent runs", description="Runs that may execute at once; the rest wait in a queue.")
register_setting(
    key="data_dir", env="FORGE_DATA_DIR", type="string", default="./data", editable=False, apply="restart",
    group="Server", label="Data directory",
    description="Folder holding the database and run artifacts; it can only be set in the environment.")
register_setting(
    key="trust_all", env="FORGE_TRUST_ALL", type="bool", default=False, editable=False,
    group="Server", label="Trust every client",
    description="Treat every request as local builder access; only for trusted networks and only from the environment.")

register_setting(
    key="log_json", env="FORGE_LOG_JSON", type="enum", options=["auto", "on", "off"], default="auto",
    apply="restart", env_parse=_log_json_env, env_format=lambda v: {"on": "1", "off": "0"}.get(v, ""),
    group="Observability", label="JSON run logs",
    description="Write one JSON log line per run event; auto turns it on when the output is not a terminal.")
register_setting(
    key="metrics_public", env="FORGE_METRICS_PUBLIC", type="bool", default=False,
    group="Observability", label="Public metrics",
    description="Serve the Prometheus /metrics endpoint to remote clients, not only to this machine.")

register_setting(
    key="prices", env="FORGE_PRICES", type="json", widget="prices", default={}, validate=validate_prices,
    group="Pricing", label="Model prices",
    description="Extra or overriding prices in USD per million input and output tokens, by model string or Claude id.")

register_setting(
    key="public_url", env="FORGE_PUBLIC_URL", type="url", default="", optional=True,
    group="Sharing", label="Public URL",
    description="Address others use to reach this Forge, used in share links and API examples.",
    placeholder="https://forge.example.com")


# ---------------------------------------------------------------- storage

_lock = threading.Lock()
_saved: dict[str, tuple[float, dict[str, Any]]] = {}  # db path -> (loaded at, {key: value})
SAVED_TTL = 5.0  # other processes' writes become visible after at most this many seconds
_boot: dict[str, Any] = {}  # effective value of each restart setting when the server started


def _db_path() -> Path:
    return Path(os.environ.get("FORGE_DATA_DIR") or "./data").resolve() / "forge.db"


def _conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                 "updated_at REAL NOT NULL)")
    return conn


def saved_values() -> dict[str, Any]:
    """Values saved in the database ({} when there is no database yet). Cached briefly."""
    path = _db_path()
    with _lock:
        hit = _saved.get(str(path))
        if hit and time.monotonic() - hit[0] < SAVED_TTL:
            return hit[1]
    values: dict[str, Any] = {}
    if path.exists():
        try:
            conn = _conn(path)
            try:
                for k, v in conn.execute("SELECT key, value FROM settings"):
                    try:
                        values[k] = json.loads(v)
                    except json.JSONDecodeError:
                        log.warning("saved setting %s is not valid JSON; ignoring it", k)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("could not read saved settings: %s", exc)
    with _lock:
        _saved[str(path)] = (time.monotonic(), values)
    return values


def save(values: dict[str, Any]) -> None:
    """Persists already validated values (see `validate_update`)."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _conn(path)
    try:
        now = time.time()
        for k, v in values.items():
            conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) "
                         "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (k, json.dumps(v), now))
    finally:
        conn.close()
    invalidate()


def reset(keys: list[str]) -> None:
    path = _db_path()
    if not path.exists():
        return
    conn = _conn(path)
    try:
        conn.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])
    finally:
        conn.close()
    invalidate()


def invalidate() -> None:
    with _lock:
        _saved.clear()


# ---------------------------------------------------------------- reading


@dataclass
class Resolved:
    value: Any
    source: Source
    error: str | None = None  # an environment or saved value that is invalid and was ignored
    extra: dict[str, Any] = field(default_factory=dict)


def env_value(s: Setting) -> str | None:
    raw = os.environ.get(s.env)
    return raw if raw not in (None, "") else None


def resolve(key: str) -> Resolved:
    """The effective value of a setting and where it came from: env > saved > default."""
    s = setting(key)
    if s.secret:
        raw = env_value(s)
        return Resolved(raw, "env" if raw is not None else "default")
    error = None
    raw = env_value(s)
    if raw is not None:
        try:
            return Resolved(parse_env(s, raw), "env")
        except ValueError as exc:
            # The environment still owns the setting (read only), but its value is unusable.
            return Resolved(s.default, "env", f"{s.env} {exc}; using the default")
    if s.editable:
        saved = saved_values()
        if key in saved:
            try:
                return Resolved(coerce(s, saved[key]), "saved")
            except ValueError as exc:
                error = f"saved value {exc}; using the default"
    return Resolved(s.default, "default", error)


def get(key: str) -> Any:
    """The effective value of a setting. Call it where the value is used, so live settings apply at once."""
    return resolve(key).value


def snapshot() -> None:
    """Records the value of every restart setting; called once when the server starts."""
    _boot.clear()
    for s in REGISTRY.values():
        if s.apply == "restart":
            _boot[s.key] = resolve(s.key).value


def needs_restart(key: str) -> bool:
    s = setting(key)
    return s.apply == "restart" and key in _boot and _boot[key] != resolve(key).value


def desktop() -> bool:
    return os.environ.get("FORGE_DESKTOP") == "1"


def desktop_secrets() -> set[str]:
    """Names of environment variables the desktop app filled from the Keychain."""
    return {n.strip() for n in (os.environ.get("FORGE_DESKTOP_SECRETS") or "").split(",") if n.strip()}


def validate_update(update: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Splits a PATCH body into normalized values to save and per key error messages."""
    ok, errors = {}, {}
    for key, value in update.items():
        s = REGISTRY.get(key)
        if s is None:
            errors[key] = "unknown setting"
            continue
        if s.secret:
            errors[key] = (f"API keys are never saved by Forge. Set {s.env} in the desktop app Settings window or "
                           "in .env, then restart.")
            continue
        if not s.editable:
            errors[key] = f"can only be set with the {s.env} environment variable"
            continue
        if env_value(s) is not None:
            errors[key] = f"is set by the {s.env} environment variable; remove it there to change it here"
            continue
        try:
            ok[key] = coerce(s, value)
        except ValueError as exc:
            errors[key] = str(exc)
    return ok, errors


def view(key: str) -> dict[str, Any]:
    """One setting as the API shows it. Secret values are never included, only whether they are set."""
    s = setting(key)
    r = resolve(key)
    out: dict[str, Any] = {
        "key": s.key, "env": s.env, "type": s.type, "label": s.label, "description": s.description,
        "group": s.group, "apply": s.apply, "secret": s.secret, "source": r.source,
        "editable": s.editable and not s.secret and r.source != "env",
        "needs_restart": needs_restart(key), "error": r.error,
    }
    if s.secret:
        out["is_set"] = r.value is not None
        out["from_desktop"] = r.value is not None and s.env in desktop_secrets()
        return out
    out.update(value=r.value, default=s.default)
    for k in ("options", "min", "max", "widget"):
        if getattr(s, k) is not None:
            out[k] = getattr(s, k)
    if s.optional:
        out["optional"] = True
    if s.placeholder:
        out["placeholder"] = s.placeholder
    return out


def load_all() -> None:
    """Imports every module in SETTING_MODULES so their settings are registered."""
    for name in SETTING_MODULES:
        importlib.import_module(name)


def groups() -> list[dict[str, Any]]:
    load_all()
    by: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for s in REGISTRY.values():
        by.setdefault(s.group, []).append(view(s.key))
    return [{"name": g, "settings": items} for g, items in by.items() if items]


# ---------------------------------------------------------------- helpers for call sites


def endpoints() -> list[dict]:
    return list(get("openai_endpoints") or [])


def openai_key_for(base_url: str) -> str | None:
    """The API key for an OpenAI compatible base URL: its endpoint's own variable, else OPENAI_API_KEY."""
    base = base_url.rstrip("/")
    for e in endpoints():
        if e["base_url"] == base and e.get("api_key_env"):
            return os.environ.get(e["api_key_env"]) or None
    return os.environ.get("OPENAI_API_KEY") or None


# ---------------------------------------------------------------- .env.example


def env_example() -> str:
    """The text of .env.example, generated from the registry (scripts/gen_env_example.py writes it)."""
    load_all()
    lines = [
        "# Forge settings, generated from forge/settings.py by `uv run python scripts/gen_env_example.py`.",
        "# Do not edit by hand: tests/test_settings.py fails when this file and the registry drift apart.",
        "#",
        "# Every setting except API keys and the data directory can be changed in the app (Settings page),",
        "# so most lines are commented out. A variable set here wins over the app and shows there as read only.",
    ]
    for g in GROUPS:
        items = [s for s in REGISTRY.values() if s.group == g]
        if not items:
            continue
        lines += ["", f"# ---- {g}"]
        for s in items:
            lines.append(f"# {s.label}: {s.description}" + (" Needs a restart." if s.apply == "restart" else ""))
            if s.secret:
                lines.append(f"{s.env}=")
            else:
                lines.append(f"# {s.env}={_env_default(s)}")
    lines += ["", *ENV_ONLY]
    return "\n".join(lines) + "\n"


# Variables that are not settings: read by the desktop entry point, scripts, tests and development switches.
ENV_ONLY = [
    "# ---- Environment only (not settings): desktop entry, development switches, scripts and tests",
    "# Desktop app server entry (forge/desktop_entry.py; set by the macOS app): host, port and the app's PID.",
    "# FORGE_HOST=127.0.0.1",
    "# FORGE_PORT=8000",
    "# FORGE_PARENT_PID=",
    "# Directory with example agents (default demo/ in the checkout, or the bundled copy in the desktop app).",
    "# FORGE_EXAMPLES_DIR=",
    "# 1 disables the SSRF address check for HTTP tools, remote MCP servers and webhooks (development and tests only).",
    "# FORGE_ALLOW_PRIVATE_HTTP=0",
    "# 0 disables the trigger scheduler; seconds between scheduler checks (default 15).",
    "# FORGE_SCHEDULER=1",
    "# FORGE_SCHEDULER_INTERVAL=15",
    "# Seconds between GitHub and Slack delivery retries (default 1).",
    "# FORGE_TRIGGER_RETRY_BACKOFF=1",
    "# Tests only: the scheduler reads the current unix time from this file.",
    "# FORGE_TEST_CLOCK_FILE=",
    "# Server that scripts/preflight.sh checks. Default http://localhost:8000.",
    "# FORGE_URL=http://localhost:8000",
    "# 1 runs live model tests (same as pytest -m live); agent and judge models for live Claude tests.",
    "# FORGE_LIVE=0",
    "# FORGE_LIVE_MODEL=",
    "# FORGE_LIVE_GRADER_MODEL=",
    "# OpenAI compatible server and model for the live local model test. Defaults: Ollama and qwen3:4b-instruct.",
    "# FORGE_LOCAL_BASE_URL=http://localhost:11434/v1",
    "# FORGE_LOCAL_MODEL=qwen3:4b-instruct",
    "# vLLM endpoint for the live GPU test and preflight, including /v1; model defaults to the first served.",
    "# FORGE_VLLM_URL=",
    "# FORGE_VLLM_MODEL=",
    "# Browser tests (web/playwright.config.ts): base port (servers on it, +1 and +2) and data dir.",
    "# FORGE_E2E_PORT=8011",
    "# FORGE_E2E_DATA_DIR=",
    "# Python end-to-end servers (tests/e2e): restrict their ports to a range, e.g. 8082-8089. Default: any free port.",
    "# FORGE_PYTEST_E2E_PORTS=",
]


def _env_default(s: Setting) -> str:
    if s.env_format is not None:
        return s.env_format(s.default)
    if s.type == "json":
        return json.dumps(s.default, separators=(",", ":"))
    if s.type == "bool":
        return "1" if s.default else "0"
    if s.type == "float" and float(s.default).is_integer():
        return str(int(s.default))
    return "" if s.default is None else str(s.default)
