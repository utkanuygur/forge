"""Reference for the judge case: the same README as readme-quickstart-runs."""

import shutil
import sys
from pathlib import Path

shutil.copy(Path(__file__).with_name("readme-quickstart-runs") / "README.md", Path(sys.argv[1]) / "README.md")
