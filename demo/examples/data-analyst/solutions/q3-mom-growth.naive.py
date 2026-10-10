"""Plausible but wrong: counts refunded and cancelled lines as revenue."""

import runpy
import sys

import _data

real = _data.revenue_by_region_month
_data.revenue_by_region_month = lambda ws: real(ws, ("completed", "refunded", "cancelled"))
sys.modules["_data"] = _data
runpy.run_path(str(_data.Path(__file__).with_name("q3-mom-growth.py")), run_name="__main__")
