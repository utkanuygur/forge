"""Plausible but wrong: also folds Gmail dots, which the request said not to do."""

import runpy
import sys
from pathlib import Path

sys.argv.append("--gmail")
runpy.run_path(str(Path(__file__).with_name("duplicate-customers.py")), run_name="__main__")
