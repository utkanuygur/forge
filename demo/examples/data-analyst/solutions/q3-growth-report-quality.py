"""Reference for the judge case: the same results.json and report.md as q3-mom-growth."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("q3-mom-growth.py")), run_name="__main__")
