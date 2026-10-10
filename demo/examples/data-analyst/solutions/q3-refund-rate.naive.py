"""Plausible but wrong: keeps cancelled lines in the denominator."""

import runpy
import sys
from pathlib import Path

sys.argv.append("--with-cancelled")
runpy.run_path(str(Path(__file__).with_name("q3-refund-rate.py")), run_name="__main__")
