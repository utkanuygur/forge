"""Reference for the "Add missing docstrings" suggested prompt: inserts docstrings, changes no code."""

import sys
from pathlib import Path

ws = Path(sys.argv[1])
EDITS = {
    "shelfcache/cache.py": [
        ("    def pop(self, key: Hashable, default: Any = _MISSING) -> Any:\n",
         '        """Remove `key` and return its value.\n\n'
         "        Args:\n            key: The key to remove.\n"
         "            default: Returned when the key is missing or expired.\n\n"
         "        Returns:\n            The stored value, or `default`.\n\n"
         "        Raises:\n            KeyError: If the key is missing or expired and no default is given.\n"
         '        """\n'),
        ("    def clear(self) -> None:\n",
         '        """Remove every entry and reset the stats counters."""\n'),
        ("    def purge(self) -> int:\n",
         '        """Remove every expired entry.\n\n        Returns:\n            The number of entries removed.\n'
         '        """\n'),
    ],
    "shelfcache/serialize.py": [
        ("         clock: Callable[[], float] = time.monotonic) -> TTLCache:\n",
         '    """Load a cache written by `dump()`.\n\n'
         "    Args:\n        path: File written by `dump()`.\n"
         "        maxsize: Override the saved maximum size.\n"
         "        clock: Clock for the new cache; remaining time to live counts from load time.\n\n"
         "    Returns:\n        A new TTLCache.\n\n"
         "    Raises:\n        ValueError: If the file format version is not supported.\n"
         '    """\n'),
    ],
}
for rel, edits in EDITS.items():
    path = ws / rel
    text = path.read_text()
    for anchor, doc in edits:
        assert text.count(anchor) == 1, anchor
        text = text.replace(anchor, anchor + doc)
    path.write_text(text)
