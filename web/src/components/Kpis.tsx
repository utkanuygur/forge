import type { Delta, KpiKey } from "../api";
import { fmtCount, fmtDelta, fmtMs, fmtPct, fmtUsd, TONE_CLASSES, type MetricKind } from "../lib/format";
import { PERIODS } from "../lib/period";

const KPI_DEFS: { key: KpiKey; label: string; kind: MetricKind; better: "up" | "down" | "none"; fmt: (v: number | null) => string }[] = [
  { key: "runs", label: "Runs", kind: "count", better: "none", fmt: (v) => fmtCount(v) },
  { key: "success_rate", label: "Success rate", kind: "rate", better: "up", fmt: (v) => fmtPct(v, 1) },
  { key: "p95_ms", label: "p95 duration", kind: "duration", better: "down", fmt: (v) => fmtMs(v) },
  { key: "cost_usd", label: "Spend", kind: "usd", better: "none", fmt: (v) => fmtUsd(v) },
  { key: "thumbs_up_rate", label: "Thumbs up rate", kind: "rate", better: "up", fmt: (v) => fmtPct(v, 0) },
];

export function KpiRow({ kpis, periodLabel, notes = {} }: { kpis: Record<KpiKey, Delta>; periodLabel: string; notes?: Partial<Record<KpiKey, string>> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" data-testid="kpi-row">
      {KPI_DEFS.map((k) => {
        const d = kpis[k.key];
        const delta = fmtDelta(d, k.kind, k.better);
        return (
          <div key={k.key} className="card min-w-0 px-4 py-3" data-testid="kpi" data-kpi={k.key}>
            <div className="text-xs text-neutral-500 dark:text-neutral-400">{k.label}</div>
            <div className="mt-1 truncate text-2xl font-semibold tracking-tight" data-testid="kpi-value">
              {k.fmt(d?.value ?? null)}
            </div>
            <div className={`mt-0.5 text-xs ${TONE_CLASSES[delta.tone]}`}>
              {delta.text}
              {delta.text !== "no prior data" && <span className="text-neutral-400 dark:text-neutral-500"> vs previous {periodLabel}</span>}
            </div>
            {notes[k.key] && <div className="mt-0.5 text-[11px] text-neutral-400 dark:text-neutral-500">{notes[k.key]}</div>}
          </div>
        );
      })}
    </div>
  );
}

export function PeriodSelector({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-neutral-300 bg-white p-0.5 dark:border-neutral-700 dark:bg-neutral-900" role="group" aria-label="Period">
      {PERIODS.map((p) => (
        <button
          key={p.key}
          type="button"
          aria-pressed={days === p.days}
          onClick={() => onChange(p.days)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
            days === p.days
              ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
              : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
