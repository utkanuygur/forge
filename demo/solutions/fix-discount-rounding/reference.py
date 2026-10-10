from _util import append, changelog, edit

edit("src/acme/pricing.py", "    return subtotal * percent // 100\n",
     "    # Round to the nearest cent; exact halves round up (in the customer's favor).\n"
     "    return (subtotal * percent + 50) // 100\n")
append("tests/test_pricing.py", '''

def test_discount_rounds_half_up() -> None:
    assert discount_cents(1010, 15) == 152
    assert discount_cents(25, 10) == 3
    assert discount_cents(999, 33) == 330
    assert discount_cents(12345, 7) == 864
''')
changelog("Fixed percentage discounts rounding down; they now round to the nearest cent.")
