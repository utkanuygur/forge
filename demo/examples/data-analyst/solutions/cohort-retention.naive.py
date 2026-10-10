"""Plausible but wrong: counts any order line as activity, including refunded and cancelled ones."""

import runpy
import sys
from pathlib import Path

sys.argv.append("--any-status")
runpy.run_path(str(Path(__file__).with_name("cohort-retention.py")), run_name="__main__")
