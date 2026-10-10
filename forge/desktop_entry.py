"""Server entry point for the Forge desktop app and its frozen (PyInstaller) build.

Usage: python -m forge.desktop_entry [--host 127.0.0.1] [--port 8000]
Host and port also come from FORGE_HOST and FORGE_PORT. When FORGE_PARENT_PID is set, the server
exits on its own once that process is gone, so a crashed desktop app never leaves it running.
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
import time


def _watch_parent(pid: int) -> None:
    while True:
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        except PermissionError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="forge-server")
    parser.add_argument("--host", default=os.environ.get("FORGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FORGE_PORT", "8000")))
    args = parser.parse_args()

    parent = os.environ.get("FORGE_PARENT_PID", "")
    if parent.isdigit():
        threading.Thread(target=_watch_parent, args=(int(parent),), daemon=True).start()

    import uvicorn

    from forge.server.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
