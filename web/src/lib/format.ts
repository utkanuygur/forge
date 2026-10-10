// Number, duration, money and KPI delta formatting for the observability screens.

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "n/a";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "unknown";
  if (v === 0) return "$0";
  const abs = Math.abs(v);
  if (abs < 0.01) return `$${v.toFixed(4)}`;
  if (abs < 100) return `$${v.toFixed(2)}`;
  return `$${Math.round(v).toLocaleString()}`;
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "n/a";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtCount(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  if (Math.abs(v) >= 10_000) return `${(v / 1000).toFixed(v >= 100_000 ? 0 : 1)}K`;
  return Math.round(v).toLocaleString();
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "n/a";
  return v.toFixed(digits);
}

export function fmtAgo(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "never";
  const s = Math.max(0, now - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export type MetricKind = "count" | "rate" | "duration" | "usd";
export type Tone = "good" | "bad" | "neutral";

export interface DeltaInput {
  value: number | null;
  previous: number | null;
  change: number | null;
  change_pct: number | null;
}

/**
 * Change versus the previous period as short text and a tone. Rates change in percentage points,
 * counts, durations and money in percent. `better` says which direction is good ("up", "down" or "none").
 */
export function fmtDelta(d: DeltaInput | null | undefined, kind: MetricKind, better: "up" | "down" | "none"): { text: string; tone: Tone } {
  if (!d || d.change === null || d.value === null || d.previous === null) return { text: "no prior data", tone: "neutral" };
  if (d.change === 0) return { text: "no change", tone: "neutral" };
  const sign = d.change > 0 ? "+" : "−"; // minus sign, not a hyphen
  let text: string;
  if (kind === "rate") {
    text = `${sign}${Math.abs(d.change * 100).toFixed(1)} pts`;
  } else if (d.change_pct === null) {
    // No previous base to divide by: show the absolute change.
    const abs = Math.abs(d.change);
    text = `${sign}${kind === "usd" ? fmtUsd(abs) : kind === "duration" ? fmtMs(abs) : fmtCount(abs)}`;
  } else {
    const pct = Math.abs(d.change_pct * 100);
    if (pct < 0.05) return { text: "no change", tone: "neutral" };
    text = `${sign}${pct >= 10 ? Math.round(pct) : pct.toFixed(1)}%`;
  }
  const up = d.change > 0;
  const tone: Tone = better === "none" ? "neutral" : (better === "up") === up ? "good" : "bad";
  return { text, tone };
}

export const TONE_CLASSES: Record<Tone, string> = {
  good: "text-emerald-700 dark:text-emerald-400",
  bad: "text-red-700 dark:text-red-400",
  neutral: "text-neutral-500 dark:text-neutral-400",
};
