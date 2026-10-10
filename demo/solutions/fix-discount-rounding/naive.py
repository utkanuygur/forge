# Correct behavior, but never ran `make check`: unused import and an overlong line in the test.
from _util import edit, write

edit("src/acme/pricing.py", "from collections.abc import Iterable\n",
     "import math\nfrom collections.abc import Iterable\n")
edit("src/acme/pricing.py", "    return subtotal * percent // 100\n",
     "    return math.floor(subtotal * percent / 100 + 0.5)\n")
write("tests/test_discount_rounding.py", '''import pytest

from acme.pricing import discount_cents


def test_discount_rounds_half_up() -> None:
    assert discount_cents(1010, 15) == 152, "a $10.10 order with 15% off should get $1.52 off, not $1.51"
    assert discount_cents(25, 10) == 3
''')
