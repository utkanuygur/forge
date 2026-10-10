// Pure scale and tick helpers for the hand-rolled SVG charts.

/** A "nice" step (1, 2, 2.5 or 5 times a power of ten) close to range / count. */
export function niceStep(range: number, count: number): number {
  if (!(range > 0) || count < 1) return 1;
  const raw = range / count;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / pow;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * pow;
}

/** Ticks from 0 to a nice maximum that covers `max` (about `count` intervals). Integer ticks when `integer`. */
export function niceTicks(max: number, count = 4, integer = false): number[] {
  if (!(max > 0)) return [0, 1];
  let step = niceStep(max, count);
  if (integer) step = Math.max(1, Math.ceil(step));
  const top = Math.ceil(max / step - 1e-9) * step;
  const out: number[] = [];
  for (let v = 0; v <= top + step / 1e6; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/** Linear map from a domain to a range. */
export function linear(domain: [number, number], range: [number, number]): (v: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (v: number) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** Band layout for `n` equal slots across `width`: slot x, slot width and a bar width capped at `maxBar`. */
export function bands(n: number, width: number, maxBar = 24, padRatio = 0.3): { x: (i: number) => number; slot: number; bar: number } {
  const slot = n > 0 ? width / n : width;
  const bar = Math.max(1, Math.min(maxBar, slot * (1 - padRatio)));
  return { x: (i: number) => i * slot + (slot - bar) / 2, slot, bar };
}

/** Indexes of buckets to label so at most `maxLabels` labels show, always including the last. */
export function labelIndexes(n: number, maxLabels: number): number[] {
  if (n <= 0) return [];
  if (n <= maxLabels) return Array.from({ length: n }, (_, i) => i);
  const every = Math.ceil(n / Math.max(1, maxLabels - 1));
  const out: number[] = [];
  for (let i = n - 1; i >= 0; i -= every) out.unshift(i);
  return out;
}

/** Label for a bucket start (unix seconds): "Oct 3" for days, "14:00" for hours. */
export function bucketLabel(t: number, bucket: "hour" | "day"): string {
  const d = new Date(t * 1000);
  if (bucket === "hour") return `${String(d.getHours()).padStart(2, "0")}:00`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Fractional bucket position of a timestamp within a series of equal buckets starting at `first`. */
export function timeToIndex(ts: number, first: number, size: number): number {
  return (ts - first) / size;
}
