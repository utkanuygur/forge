import { useCallback, useState } from "react";

export const PERIODS = [
  { key: "1", label: "24h", days: 1 },
  { key: "7", label: "7d", days: 7 },
  { key: "14", label: "14d", days: 14 },
  { key: "30", label: "30d", days: 30 },
] as const;

export const PERIOD_KEY = "forge.period";
const DEFAULT_DAYS = 14;

export function readPeriod(): number {
  try {
    const raw = window.localStorage.getItem(PERIOD_KEY);
    const n = raw ? Number(raw) : NaN;
    return PERIODS.some((p) => p.days === n) ? n : DEFAULT_DAYS;
  } catch {
    return DEFAULT_DAYS;
  }
}

export function writePeriod(days: number): void {
  try {
    window.localStorage.setItem(PERIOD_KEY, String(days));
  } catch {
    /* storage unavailable (private mode or blocked): keep the in-memory value */
  }
}

/** The selected period in days, remembered per browser. */
export function usePeriod(): [number, (days: number) => void] {
  const [days, setDays] = useState<number>(readPeriod);
  const set = useCallback((d: number) => {
    setDays(d);
    writePeriod(d);
  }, []);
  return [days, set];
}

export function periodLabel(days: number): string {
  return PERIODS.find((p) => p.days === days)?.label ?? `${days}d`;
}

/** Minutes east of UTC, for local day buckets on the server. */
export function tzOffsetMin(): number {
  return -new Date().getTimezoneOffset();
}
