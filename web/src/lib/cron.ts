// Client side cron preview. Mirrors forge/triggers/cron.py: 5 fields, names, ranges, steps, macros,
// day-of-month OR day-of-week when both are restricted, and the same daylight saving rules.

export interface Cron {
  minutes: Set<number>;
  hours: Set<number>;
  days: Set<number>;
  months: Set<number>;
  weekdays: Set<number>;
  domStar: boolean;
  dowStar: boolean;
  fields: string[];
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
export const MACROS: Record<string, string> = {
  "@hourly": "0 * * * *",
  "@daily": "0 0 * * *",
  "@midnight": "0 0 * * *",
  "@weekly": "0 0 * * 0",
  "@monthly": "0 0 1 * *",
  "@yearly": "0 0 1 1 *",
  "@annually": "0 0 1 1 *",
};

export const PRESETS: { label: string; cron: string }[] = [
  { label: "Every weekday at 9:00", cron: "0 9 * * 1-5" },
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Every day at 9:00", cron: "0 9 * * *" },
  { label: "Every Monday at 9:00", cron: "0 9 * * 1" },
  { label: "Every 15 minutes", cron: "*/15 * * * *" },
];

const SPEC: [string, number, number, string[] | null][] = [
  ["minute", 0, 59, null],
  ["hour", 0, 23, null],
  ["day of month", 1, 31, null],
  ["month", 1, 12, MONTHS],
  ["day of week", 0, 7, DAYS],
];

export class CronError extends Error {}

function value(text: string, lo: number, hi: number, names: string[] | null, field: string): number {
  const t = text.toLowerCase();
  if (names && names.includes(t)) return names.indexOf(t) + (field === "month" ? 1 : 0);
  if (!/^\d+$/.test(t)) throw new CronError(`invalid ${field} value "${text}"`);
  const v = Number(t);
  if (v < lo || v > hi) throw new CronError(`${field} value ${v} out of range ${lo} to ${hi}`);
  return v;
}

function field(text: string, lo: number, hi: number, names: string[] | null, name: string): Set<number> {
  if (!text) throw new CronError(`empty ${name} field`);
  const out = new Set<number>();
  for (const part of text.split(",")) {
    const [base, stepS, extra] = part.split("/");
    if (extra !== undefined) throw new CronError(`invalid step in ${name}`);
    let step = 1;
    if (stepS !== undefined) {
      if (!/^\d+$/.test(stepS) || Number(stepS) < 1) throw new CronError(`invalid step "${stepS}" in ${name}`);
      step = Number(stepS);
    }
    let start: number;
    let end: number;
    if (base === "*") {
      start = lo;
      end = hi;
    } else if (base.includes("-")) {
      const [a, b] = base.split("-");
      start = value(a, lo, hi, names, name);
      end = value(b, lo, hi, names, name);
      if (start > end) throw new CronError(`invalid range "${base}" in ${name}`);
    } else {
      start = value(base, lo, hi, names, name);
      end = stepS !== undefined ? hi : start;
    }
    for (let v = start; v <= end; v += step) out.add(v);
  }
  return out;
}

export function parseCron(expr: string): Cron {
  const raw = (expr || "").trim().split(/\s+/).join(" ");
  const text = MACROS[raw.toLowerCase()] ?? raw;
  const parts = text.split(" ");
  if (parts.length !== 5 || !text) {
    throw new CronError("A cron expression has 5 fields: minute hour day-of-month month day-of-week");
  }
  const sets = parts.map((p, i) => field(p, SPEC[i][1], SPEC[i][2], SPEC[i][3], SPEC[i][0]));
  const c: Cron = {
    minutes: sets[0],
    hours: sets[1],
    days: sets[2],
    months: sets[3],
    weekdays: new Set([...sets[4]].map((d) => d % 7)),
    domStar: parts[2].startsWith("*"),
    dowStar: parts[4].startsWith("*"),
    fields: parts,
  };
  if (nextNaive(c, Date.UTC(2000, 0, 1)) === null) throw new CronError("This expression never fires");
  return c;
}

// Naive wall-clock times are represented as UTC milliseconds of the same Y-M-D h:m.
const MIN = 60_000;
const HOUR = 60 * MIN;

function dayOk(c: Cron, d: Date): boolean {
  const dom = c.days.has(d.getUTCDate());
  const dow = c.weekdays.has(d.getUTCDay());
  return c.domStar || c.dowStar ? dom && dow : dom || dow;
}

function nextNaive(c: Cron, from: number): number | null {
  let t = Math.ceil(from / MIN) * MIN;
  const limit = new Date(t).getUTCFullYear() + 30;
  for (;;) {
    const d = new Date(t);
    if (d.getUTCFullYear() > limit) return null;
    if (!c.months.has(d.getUTCMonth() + 1)) t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
    else if (!dayOk(c, d)) t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
    else if (!c.hours.has(d.getUTCHours()))
      t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours() + 1);
    else if (!c.minutes.has(d.getUTCMinutes())) t += MIN;
    else return t;
  }
}

const formatters = new Map<string, Intl.DateTimeFormat>();

export function validTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz || "UTC" });
    return true;
  } catch {
    return false;
  }
}

/** Wall clock of `instant` in `tz`, as a naive timestamp. */
function wall(instant: number, tz: string): number {
  let f = formatters.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });
    formatters.set(tz, f);
  }
  const p: Record<string, number> = {};
  for (const part of f.formatToParts(new Date(instant))) if (part.type !== "literal") p[part.type] = Number(part.value);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second);
}

function instants(c: Cron, naive: number, tz: string): number[] {
  const before = naive - (wall(naive - 6 * HOUR, tz) - (naive - 6 * HOUR));
  const after = naive - (wall(naive + 6 * HOUR, tz) - (naive + 6 * HOUR));
  const valid = [...new Set([before, after])].filter((i) => wall(i, tz) === naive).sort((a, b) => a - b);
  if (valid.length === 0) return [before]; // nonexistent: shifted forward by the gap
  return c.hours.size === 24 ? valid : [valid[0]];
}

/** First fire instant (ms) strictly after `from` (ms). */
export function nextAfter(c: Cron, from: number, tz = "UTC"): number | null {
  let t = wall(from, tz) - 3 * HOUR;
  let best: number | null = null;
  let firstNaive: number | null = null;
  for (;;) {
    const m = nextNaive(c, t);
    if (m === null || (firstNaive !== null && m > firstNaive + 3 * HOUR)) return best;
    for (const i of instants(c, m, tz)) {
      if (i > from && (best === null || i < best)) {
        best = i;
        if (firstNaive === null) firstNaive = m;
      }
    }
    t = m + MIN;
  }
}

export function nextTimes(expr: string, tz: string, from: number, n = 3): number[] {
  const c = parseCron(expr);
  const out: number[] = [];
  let cur = from;
  for (let k = 0; k < n; k++) {
    const nx = nextAfter(c, cur, tz);
    if (nx === null) break;
    out.push(nx);
    cur = nx;
  }
  return out;
}

function hhmm(h: number, m: number): string {
  return `${h}:${String(m).padStart(2, "0")}`;
}

function listWords(xs: string[]): string {
  if (xs.length <= 1) return xs.join("");
  return `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

const single = (s: string) => /^\d+$/.test(s);

/** A short human description, for example "Every weekday at 9:00". Falls back to "Custom schedule". */
export function describeCron(expr: string): string {
  let c: Cron;
  try {
    c = parseCron(expr);
  } catch {
    return "Invalid schedule";
  }
  const [mi, ho, dom, mon, dow] = c.fields;
  const allDays = dom === "*" && mon === "*";
  if (mi === "*" && ho === "*" && allDays && dow === "*") return "Every minute";
  const step = /^\*\/(\d+)$/.exec(mi);
  if (step && ho === "*" && allDays && dow === "*") return `Every ${step[1]} minutes`;
  if (single(mi) && ho === "*" && allDays && dow === "*") {
    return mi === "0" ? "Every hour" : `Every hour at minute ${Number(mi)}`;
  }
  if (!single(mi) || !single(ho) || mon !== "*") return "Custom schedule";
  const at = `at ${hhmm(Number(ho), Number(mi))}`;
  if (dom === "*" && dow === "*") return `Every day ${at}`;
  if (dom === "*") {
    const wd = [...c.weekdays].sort((a, b) => a - b);
    if (wd.join() === "1,2,3,4,5") return `Every weekday ${at}`;
    if (wd.join() === "0,6") return `Every weekend day ${at}`;
    return `Every ${listWords(wd.map((d) => DAY_NAMES[d]))} ${at}`;
  }
  if (single(dom) && dow === "*") return `On day ${Number(dom)} of every month ${at}`;
  return "Custom schedule";
}

export function formatFireTime(ms: number, tz: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: tz || "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(ms));
}

export function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
