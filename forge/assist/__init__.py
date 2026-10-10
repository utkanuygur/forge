"""Assist: grader-written suggestions for fields users would otherwise type by hand (see CONTRACT.md "Assist").

Importing the package registers the built-in kinds (forge.assist.kinds).
"""

from forge.assist import kinds as _kinds  # noqa: F401  registers the built-in kinds
from forge.assist.engine import AssistError, StructuredGrader, run_assist
from forge.assist.registry import AssistKind, AssistRequest, Prepared, get, kinds, register

__all__ = ["AssistError", "AssistKind", "AssistRequest", "Prepared", "StructuredGrader", "get", "kinds", "register",
           "run_assist"]
