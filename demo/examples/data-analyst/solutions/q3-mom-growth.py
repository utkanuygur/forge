"""Reference: highest Q3 month over month revenue growth by region, results.json and report.md."""

from _data import month_add, revenue_by_region_month, workspace, write

ws = workspace()
rev = revenue_by_region_month(ws)
regions = sorted({r for r, _ in rev})
months = ["2026-06", "2026-07", "2026-08", "2026-09"]
growth = {(r, m): (rev[(r, m)] - rev[(r, month_add(m, -1))]) / rev[(r, month_add(m, -1))] * 100
          for r in regions for m in months[1:]}
ranked = sorted(growth, key=growth.get, reverse=True)
region, month = ranked[0]
best = growth[(region, month)]
second_region, second_month = ranked[1]
write(ws, "results.json", {
    "region": region, "month": month, "growth_pct": round(best, 2),
    "revenue": {r: {m: rev[(r, m)] / 100 for m in months} for r in regions},
})

lines = [
    "# Q3 2026 revenue growth by region",
    "",
    f"**{region} had the highest month over month revenue growth in Q3 2026: {best:.1f}% in "
    f"{month} versus the month before.** The next highest was {second_region} in {second_month} at "
    f"{growth[(second_region, second_month)]:+.1f}%.",
    "",
    "Revenue counts completed order lines only (quantity x unit price); growth compares each month with the "
    "previous month.",
    "",
    "| Region | Jun | Jul | Aug | Sep | Jul MoM | Aug MoM | Sep MoM |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
]
for r in regions:
    vals = " | ".join(f"${rev[(r, m)] / 100:,.2f}" for m in months)
    moms = " | ".join(f"{growth[(r, m)]:+.1f}%" for m in months[1:])
    lines.append(f"| {r} | {vals} | {moms} |")
lines += [
    "",
    f"Caveat: the jump is concentrated in {region} in a single month and revenue falls back in the following "
    "month, which looks like a one off event such as a campaign rather than a trend. Worth confirming with "
    "marketing before planning on it.",
]
(ws / "report.md").write_text("\n".join(lines) + "\n")
