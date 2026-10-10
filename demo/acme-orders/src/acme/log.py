"""Structured logging setup shared by the service."""

from __future__ import annotations

import logging
import os
import sys

_ROOT = "acme"
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger(_ROOT)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(os.environ.get("ACME_LOG_LEVEL", "INFO").upper())
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure()
    if name != _ROOT and not name.startswith(_ROOT + "."):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)
