"""Minimal 5-field cron parser and next-fire computation with IANA time zones.

Fields: minute (0-59), hour (0-23), day of month (1-31), month (1-12 or jan-dec), day of week (0-7 or sun-sat,
0 and 7 are Sunday). Each field accepts `*`, numbers, names, ranges `a-b`, steps `*/n`, `a-b/n`, `a/n` and comma
lists. Macros: @hourly, @daily, @midnight, @weekly, @monthly, @yearly, @annually.

Semantics follow Vixie cron where it matters:
- When both day of month and day of week are restricted (neither starts with `*`), a day matches if EITHER matches.
- Daylight saving time. A wall-clock time that does not exist (spring forward) fires shifted forward by the
  gap, so `30 2 * * *` fires at 03:30 on that day. A wall-clock time that occurs twice (fall back) fires once,
  on its first occurrence, unless the hour field is `*`, in which case the expression is interval-like and
  fires in both occurrences.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
MACROS = {
    "@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
}
FIELDS = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day of month", 1, 31, {}), ("month", 1, 12, MONTHS),
          ("day of week", 0, 7, DAYS))
SEARCH_YEARS = 30  # Feb 29 combined with a weekday can take up to 28 years to recur


class CronError(ValueError):
    pass


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f"unknown time zone {name!r}") from exc


def _value(text: str, lo: int, hi: int, names: dict[str, int], field: str) -> int:
    t = text.lower()
    if t in names:
        return names[t]
    if not t.isdigit():
        raise CronError(f"invalid {field} value {text!r}")
    v = int(t)
    if not lo <= v <= hi:
        raise CronError(f"{field} value {v} out of range {lo}-{hi}")
    return v


def _field(text: str, lo: int, hi: int, names: dict[str, int], field: str) -> set[int]:
    out: set[int] = set()
    if not text:
        raise CronError(f"empty {field} field")
    for part in text.split(","):
        base, slash, step_s = part.partition("/")
        step = 1
        if slash:
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"invalid step {step_s!r} in {field}")
            step = int(step_s)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _dash, b = base.partition("-")
            start, end = _value(a, lo, hi, names, field), _value(b, lo, hi, names, field)
            if start > end:
                raise CronError(f"invalid range {base!r} in {field}")
        else:
            start = _value(base, lo, hi, names, field)
            end = hi if slash else start
        out.update(range(start, end + 1, step))
    return out


@dataclass(frozen=True)
class Cron:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    dom_star: bool
    dow_star: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        raw = " ".join((expr or "").split())
        text = MACROS.get(raw.lower(), raw)
        parts = text.split(" ")
        if len(parts) != 5:
            raise CronError("a cron expression has 5 fields: minute hour day-of-month month day-of-week")
        sets = [_field(p, lo, hi, names, name) for p, (name, lo, hi, names) in zip(parts, FIELDS)]
        weekdays = frozenset(d % 7 for d in sets[4])
        c = cls(raw, frozenset(sets[0]), frozenset(sets[1]), frozenset(sets[2]), frozenset(sets[3]), weekdays,
                parts[2].startswith("*"), parts[4].startswith("*"))
        if c._next_naive(datetime(2000, 1, 1)) is None:
            raise CronError("this expression never fires")
        return c

    @property
    def interval_hours(self) -> bool:
        return len(self.hours) == 24

    def _day_ok(self, t: datetime) -> bool:
        dom = t.day in self.days
        dow = (t.isoweekday() % 7) in self.weekdays
        if self.dom_star or self.dow_star:
            return dom and dow
        return dom or dow

    def _next_naive(self, t: datetime) -> datetime | None:
        """First naive wall-clock minute >= t that matches every field."""
        t = t.replace(second=0, microsecond=0)
        limit = t.year + SEARCH_YEARS
        while t.year <= limit:
            if t.month not in self.months:
                t = datetime(t.year + (t.month == 12), t.month % 12 + 1, 1)
            elif not self._day_ok(t):
                t = datetime(t.year, t.month, t.day) + timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        return None

    def _instants(self, naive: datetime, tz: ZoneInfo) -> list[float]:
        a = naive.replace(tzinfo=tz, fold=0).timestamp()
        b = naive.replace(tzinfo=tz, fold=1).timestamp()
        if a == b:
            return [a]
        if datetime.fromtimestamp(a, tz).replace(tzinfo=None) != naive:
            return [a]  # nonexistent: fold=0 uses the offset before the gap, i.e. shifted forward
        return sorted({a, b}) if self.interval_hours else [min(a, b)]

    def next_after(self, ts: float, tz: ZoneInfo | str = "UTC") -> float | None:
        """The first fire instant (unix seconds) strictly after `ts`."""
        zone = get_zone(tz) if isinstance(tz, str) else tz
        start = datetime.fromtimestamp(ts, zone).replace(tzinfo=None) - timedelta(hours=3)
        best: float | None = None
        first_naive: datetime | None = None
        t = start
        while True:
            m = self._next_naive(t)
            if m is None or (first_naive is not None and m > first_naive + timedelta(hours=3)):
                return best
            for inst in self._instants(m, zone):
                if inst > ts and (best is None or inst < best):
                    best = inst
                    first_naive = first_naive or m
            t = m + timedelta(minutes=1)

    def next_n(self, ts: float, n: int, tz: ZoneInfo | str = "UTC") -> list[float]:
        out: list[float] = []
        cur = ts
        for _ in range(n):
            nxt = self.next_after(cur, tz)
            if nxt is None:
                break
            out.append(nxt)
            cur = nxt
        return out
