"""A tiny MCP server for tests.

Usage:
  python mcp_server.py stdio
  python mcp_server.py http <port>      # Streamable HTTP at http://127.0.0.1:<port>/mcp
  python mcp_server.py sse <port>       # SSE at http://127.0.0.1:<port>/sse

Tools: add(a, b), echo(text), whoami(), picture(), where(), boom(), sleep(seconds).
`whoami` reports the X-Test-Token header (HTTP) or the TEST_TOKEN env var (stdio), so tests can check secret
substitution.

Built with the official SDK (`mcp.server.mcpserver.MCPServer`). The forge-sandbox image does not ship the SDK
and sandboxes have no network, so when `mcp` is not importable the stdio mode falls back to a minimal
line-delimited JSON-RPC implementation of the same tools (initialize, tools/list, tools/call).
"""


import json
import os
import sys
import time

TOOLS = [
    {"name": "add", "description": "Add two integers.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                     "required": ["a", "b"]}},
    {"name": "echo", "description": "Echo text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "whoami", "description": "Report the test token the server received.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "picture", "description": "Return an image and a caption.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "where", "description": "Report the working directory the server runs in.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "boom", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "sleep", "description": "Sleep for some seconds.",
     "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}, "required": ["seconds"]}},
]
PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


def run_sdk(mode: str, port: int) -> None:
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.server.mcpserver.utilities.types import Image
    from mcp.server.transport_security import TransportSecuritySettings

    server = MCPServer("forge-test")

    @server.tool()
    def add(a: int, b: int) -> str:
        """Add two integers."""
        return str(a + b)

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back."""
        return text

    @server.tool()
    def whoami(ctx: Context) -> str:
        """Report the test token the server received."""
        token = os.environ.get("TEST_TOKEN", "")
        try:
            req = ctx.request_context.request
            if req is not None and getattr(req, "headers", None) is not None:
                token = req.headers.get("x-test-token", token)
        except Exception:  # noqa: BLE001
            pass
        return f"token={token}"

    @server.tool()
    def picture() -> list:
        """Return an image and a caption."""
        import base64

        return [Image(data=base64.b64decode(PNG_1PX), format="png"), "a 1px picture"]

    @server.tool()
    def where() -> str:
        """Report the working directory the server runs in."""
        return f"cwd={os.getcwd()}"

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("boom went the tool")

    @server.tool()
    async def sleep(seconds: float) -> str:
        """Sleep for some seconds."""
        import anyio

        await anyio.sleep(seconds)
        return "slept"

    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if mode == "stdio":
        server.run("stdio")
    elif mode == "http":
        server.run("streamable-http", host="127.0.0.1", port=port, transport_security=security)
    elif mode == "sse":
        server.run("sse", host="127.0.0.1", port=port, transport_security=security)
    else:
        raise SystemExit(f"unknown mode {mode!r}")


def run_minimal_stdio() -> None:
    """Fallback stdio server without the SDK (newline-delimited JSON-RPC 2.0)."""

    def reply(msg_id, result=None, error=None) -> None:
        out = {"jsonrpc": "2.0", "id": msg_id}
        out.update({"error": error} if error else {"result": result})
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()

    def text(t: str, is_error: bool = False) -> dict:
        return {"content": [{"type": "text", "text": t}], "isError": is_error}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if msg_id is None:
            continue  # notification
        if method == "initialize":
            reply(msg_id, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                           "capabilities": {"tools": {}}, "serverInfo": {"name": "forge-test-min", "version": "1"}})
        elif method == "ping":
            reply(msg_id, {})
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "add":
                reply(msg_id, text(str(int(args["a"]) + int(args["b"]))))
            elif name == "echo":
                reply(msg_id, text(str(args.get("text", ""))))
            elif name == "whoami":
                reply(msg_id, text(f"token={os.environ.get('TEST_TOKEN', '')}"))
            elif name == "picture":
                reply(msg_id, {"content": [{"type": "image", "data": PNG_1PX, "mimeType": "image/png"},
                                           {"type": "text", "text": "a 1px picture"}], "isError": False})
            elif name == "where":
                reply(msg_id, text(f"cwd={os.getcwd()}"))
            elif name == "boom":
                reply(msg_id, text("boom went the tool", True))
            elif name == "sleep":
                time.sleep(float(args.get("seconds", 0)))
                reply(msg_id, text("slept"))
            else:
                reply(msg_id, error={"code": -32602, "message": f"unknown tool {name}"})
        else:
            reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    try:
        import mcp  # noqa: F401

        have_sdk = os.environ.get("FORCE_MINIMAL") != "1"
    except ImportError:
        have_sdk = False
    if have_sdk:
        run_sdk(mode, port)
    elif mode == "stdio":
        run_minimal_stdio()
    else:
        raise SystemExit("HTTP modes need the mcp SDK")
