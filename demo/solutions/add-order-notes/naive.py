# Correct behavior and clean lint, but no CHANGELOG entry for the new and changed routes.
import runpy
import sys
from pathlib import Path

import _util

_util.changelog = lambda entry: None
sys.argv = sys.argv[:2]
runpy.run_path(str(Path(__file__).with_name("reference.py")), run_name="__main__")
