// Hand-rolled SVG charts: stacked bars over time, a line over time, sparklines and the shared chrome.
// Drawn to scale from a zero baseline, sized in real pixels (ResizeObserver) so text never scales down.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { BarChart3 } from "lucide-react";
import { bands, bucketLabel, labelIndexes, linear, niceTicks, timeToIndex } from "../../lib/scale";

export interface SeriesKey {
  key: string;
  label: string;
  /** Tailwind fill class for marks (and swatches). */
  fill: string;
}

export interface Marker {
  at: number; // unix seconds
  label: string;
}

export interface Point {
  t: number;
  [key: string]: number | null;
}

export const STATUS_KEYS: SeriesKey[] = [
  { key: "succeeded", label: "Succeeded", fill: "fill-emerald-500 dark:fill-emerald-600" },
  { key: "failed", label: "Failed", fill: "fill-red-500 dark:fill-red-600" },
  { key: "timeout", label: "Timeout", fill: "fill-amber-500" },
  { key: "budget_exceeded", label: "Over budget", fill: "fill-orange-700 dark:fill-orange-600" },
  { key: "in_progress", label: "In progress", fill: "fill-blue-500" },
];

export const SWATCH_BG: Record<string, string> = {
  succeeded: "bg-emerald-500 dark:bg-emerald-600",
  failed: "bg-red-500 dark:bg-red-600",
  timeout: "bg-amber-500",
  budget_exceeded: "bg-orange-700 dark:bg-orange-600",
  in_progress: "bg-blue-500",
  spend: "bg-accent-500",
  model: "bg-accent-500",
  tool: "bg-teal-500 dark:bg-teal-600",
};

const GRID = "stroke-neutral-200 dark:stroke-neutral-800";
const AXIS_TEXT = "fill-neutral-500 dark:fill-neutral-400";
const M = { top: 18, right: 8, bottom: 24, left: 40 };

export function useWidth<T extends HTMLElement>(fallback = 640): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const width = el.getBoundingClientRect().width;
      if (width > 0) setW(Math.floor(width));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** Path for a bar with 4px rounded data end (top) and a square baseline. */
export function topRoundedRect(x: number, y: number, w: number, h: number, r = 4): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function Legend({ keys }: { keys: { label: string; swatch: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-600 dark:text-neutral-400" aria-label="Legend">
      {keys.map((k) => (
        <li key={k.label} className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-2.5 w-2.5 rounded-sm ${k.swatch}`} aria-hidden />
          {k.label}
        </li>
      ))}
    </ul>
  );
}

export function EmptyChart({ height = 180, message = "No runs in this period yet" }: { height?: number; message?: string }) {
  return (
    <div
      data-testid="chart-empty"
      className="relative flex flex-col items-center justify-center gap-2 overflow-hidden rounded-lg border border-dashed border-neutral-200 text-sm text-neutral-500 dark:border-neutral-800 dark:text-neutral-400"
      style={{ height }}
    >
      <div className="pointer-events-none absolute inset-x-6 bottom-6 top-6 flex flex-col justify-between" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="border-t border-neutral-100 dark:border-neutral-800/70" />
        ))}
      </div>
      <BarChart3 className="relative h-5 w-5 text-neutral-400" aria-hidden />
      <span className="relative">{message}</span>
    </div>
  );
}

export function ChartCard({
  title,
  subtitle,
  legend,
  children,
  table,
  testId,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: ReactNode;
  children: ReactNode;
  table?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="card min-w-0 p-4 sm:p-5" aria-label={title} data-testid={testId}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">{subtitle}</p>}
        </div>
        {legend}
      </div>
      {children}
      {table && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer select-none text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200">View as table</summary>
          <div className="mt-2 max-h-64 overflow-auto">{table}</div>
        </details>
      )}
    </section>
  );
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.max(0, Math.min(x - 80, width - 168));
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute top-0 z-10 w-[168px] rounded-lg border border-neutral-200 bg-white/95 px-2.5 py-2 text-xs shadow-md dark:border-neutral-700 dark:bg-neutral-900/95"
      style={{ left }}
    >
      {children}
    </div>
  );
}

function YAxis({ ticks, y, width, fmt }: { ticks: number[]; y: (v: number) => number; width: number; fmt: (v: number) => string }) {
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} className={GRID} strokeWidth={1} shapeRendering="crispEdges" />
          <text x={M.left - 6} y={y(t)} dy="0.32em" textAnchor="end" className={`${AXIS_TEXT} text-[11px] tabular-nums`}>
            {fmt(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

function XLabels({ points, bucket, x, slot, width, height }: { points: Point[]; bucket: "hour" | "day"; x: (i: number) => number; slot: number; width: number; height: number }) {
  const idx = labelIndexes(points.length, Math.max(2, Math.floor((width - M.left) / 64)));
  return (
    <g>
      {idx.map((i) => {
        const cx = x(i) + slot / 2;
        const anchor = i === points.length - 1 && cx > width - 30 ? "end" : "middle";
        return (
          <text key={i} x={Math.min(cx, width - M.right)} y={height - 6} textAnchor={anchor} className={`${AXIS_TEXT} text-[11px] tabular-nums`}>
            {bucketLabel(points[i].t, bucket)}
          </text>
        );
      })}
    </g>
  );
}

function Markers({ markers, points, size, left, slot, top, bottom, width }: { markers: Marker[]; points: Point[]; size: number; left: number; slot: number; top: number; bottom: number; width: number }) {
  if (!points.length) return null;
  const first = points[0].t;
  return (
    <g data-testid="promotion-markers">
      {markers.map((m, i) => {
        const pos = timeToIndex(m.at, first, size);
        if (pos < 0 || pos > points.length) return null;
        const xx = Math.round(left + pos * slot) + 0.5;
        const anchor = xx > width - 40 ? "end" : "start";
        return (
          <g key={`${m.at}-${i}`} data-testid="promotion-marker" data-label={m.label}>
            <line x1={xx} x2={xx} y1={top - 4} y2={bottom} className="stroke-violet-500 dark:stroke-violet-400" strokeWidth={1} />
            <text x={anchor === "start" ? xx + 3 : xx - 3} y={top - 7} textAnchor={anchor} className="fill-violet-700 text-[10px] font-medium dark:fill-violet-300">
              {m.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** Stacked columns per time bucket (one key gives a plain column chart). */
export function StackedBarChart({
  points,
  keys,
  bucket,
  bucketSize,
  markers = [],
  height = 200,
  yFormat = (v) => String(v),
  integer = true,
  label,
  tooltipFormat = (v) => String(v),
}: {
  points: Point[];
  keys: SeriesKey[];
  bucket: "hour" | "day";
  bucketSize: number;
  markers?: Marker[];
  height?: number;
  yFormat?: (v: number) => string;
  integer?: boolean;
  label: string;
  tooltipFormat?: (v: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const totals = points.map((p) => keys.reduce((s, k) => s + ((p[k.key] as number) || 0), 0));
  const max = Math.max(0, ...totals);
  const ticks = niceTicks(max, 4, integer);
  const plotW = Math.max(10, width - M.left - M.right);
  const y = linear([0, ticks[ticks.length - 1]], [height - M.bottom, M.top]);
  const b = bands(points.length, plotW, 24, points.length > 40 ? 0.15 : 0.3);
  const x = (i: number) => M.left + b.x(i);
  const GAP = 2;
  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label={label} className="block overflow-visible">
        <YAxis ticks={ticks} y={y} width={width} fmt={yFormat} />
        {hover !== null && (
          <rect x={M.left + hover * b.slot} y={M.top} width={b.slot} height={height - M.top - M.bottom} className="fill-neutral-100 dark:fill-neutral-800/60" />
        )}
        {points.map((p, i) => {
          // Segments stack from the baseline; the stack top sits exactly at the scaled total and each upper
          // segment gives up 2px to a surface gap below it.
          let base = y(0);
          const segs = keys.map((k) => ({ k, v: (p[k.key] as number) || 0 })).filter((s) => s.v > 0);
          return (
            <g key={p.t} data-testid="bar" data-t={p.t} data-total={totals[i]}>
              {segs.map((s, j) => {
                const px = y(0) - y(s.v);
                const top = base - px;
                const h = Math.max(1, px - (j > 0 ? GAP : 0));
                base = top;
                return j === segs.length - 1 ? (
                  <path key={s.k.key} d={topRoundedRect(x(i), top, b.bar, h)} className={s.k.fill} />
                ) : (
                  <rect key={s.k.key} x={x(i)} y={top} width={b.bar} height={h} className={s.k.fill} />
                );
              })}
            </g>
          );
        })}
        <line x1={M.left} x2={width - M.right} y1={y(0) + 0.5} y2={y(0) + 0.5} className="stroke-neutral-300 dark:stroke-neutral-700" strokeWidth={1} />
        <Markers markers={markers} points={points} size={bucketSize} left={M.left} slot={b.slot} top={M.top} bottom={height - M.bottom} width={width} />
        <XLabels points={points} bucket={bucket} x={(i) => M.left + i * b.slot} slot={b.slot} width={width} height={height} />
        {points.map((p, i) => (
          <rect
            key={`hit-${p.t}`}
            x={M.left + i * b.slot}
            y={M.top}
            width={b.slot}
            height={height - M.top - M.bottom}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
          />
        ))}
      </svg>
      {hover !== null && points[hover] && (
        <Tooltip x={M.left + hover * b.slot + b.slot / 2} width={width}>
          <div className="mb-1 font-medium">{bucketLabel(points[hover].t, bucket)}</div>
          {keys.map((k) => (
            <div key={k.key} className="flex items-center justify-between gap-2 tabular-nums">
              <span className="inline-flex items-center gap-1.5 text-neutral-600 dark:text-neutral-400">
                <span className={`inline-block h-2 w-2 rounded-sm ${SWATCH_BG[k.key] ?? ""}`} />
                {k.label}
              </span>
              <span>{tooltipFormat((points[hover][k.key] as number) || 0)}</span>
            </div>
          ))}
        </Tooltip>
      )}
    </div>
  );
}

/** A single series over time (gaps where the value is null), 2px line with end markers. */
export function LineChart({
  points,
  valueKey,
  bucket,
  bucketSize,
  markers = [],
  height = 200,
  domainMax = 1,
  yFormat,
  label,
}: {
  points: Point[];
  valueKey: string;
  bucket: "hour" | "day";
  bucketSize: number;
  markers?: Marker[];
  height?: number;
  domainMax?: number;
  yFormat: (v: number) => string;
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const ticks = niceTicks(domainMax, 4);
  const plotW = Math.max(10, width - M.left - M.right);
  const slot = points.length ? plotW / points.length : plotW;
  const y = linear([0, ticks[ticks.length - 1]], [height - M.bottom, M.top]);
  const cx = (i: number) => M.left + i * slot + slot / 2;
  const segments: string[] = [];
  let cur = "";
  points.forEach((p, i) => {
    const v = p[valueKey];
    if (v === null || v === undefined) {
      if (cur) segments.push(cur);
      cur = "";
      return;
    }
    cur += `${cur ? "L" : "M"}${cx(i).toFixed(1)},${y(v as number).toFixed(1)}`;
  });
  if (cur) segments.push(cur);
  const dots = points.map((p, i) => ({ i, v: p[valueKey] })).filter((d) => d.v !== null && d.v !== undefined);
  return (
    <div ref={ref} className="relative w-full" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label={label} className="block overflow-visible">
        <YAxis ticks={ticks} y={y} width={width} fmt={yFormat} />
        <line x1={M.left} x2={width - M.right} y1={y(0) + 0.5} y2={y(0) + 0.5} className="stroke-neutral-300 dark:stroke-neutral-700" strokeWidth={1} />
        {hover !== null && (
          <line x1={cx(hover)} x2={cx(hover)} y1={M.top} y2={height - M.bottom} className="stroke-neutral-300 dark:stroke-neutral-600" strokeWidth={1} />
        )}
        {segments.map((d, i) => (
          <path key={i} d={d} fill="none" className="stroke-accent-500" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {dots.map((d) => (
          <circle
            key={d.i}
            data-testid="line-point"
            cx={cx(d.i)}
            cy={y(d.v as number)}
            r={hover === d.i ? 5 : 4}
            className="fill-accent-500 stroke-white dark:stroke-neutral-900"
            strokeWidth={2}
          />
        ))}
        <Markers markers={markers} points={points} size={bucketSize} left={M.left} slot={slot} top={M.top} bottom={height - M.bottom} width={width} />
        <XLabels points={points} bucket={bucket} x={(i) => M.left + i * slot} slot={slot} width={width} height={height} />
        {points.map((p, i) => (
          <rect key={`hit-${p.t}`} x={M.left + i * slot} y={M.top} width={slot} height={height - M.top - M.bottom} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && points[hover] && (
        <Tooltip x={cx(hover)} width={width}>
          <div className="mb-1 font-medium">{bucketLabel(points[hover].t, bucket)}</div>
          <div className="tabular-nums">
            {points[hover][valueKey] === null || points[hover][valueKey] === undefined ? "No finished runs" : yFormat(points[hover][valueKey] as number)}
          </div>
        </Tooltip>
      )}
    </div>
  );
}

/** Tiny trend of counts: a 2px line with an end dot; flat zero line when empty. */
export function Sparkline({ values, width = 96, height = 28, label }: { values: number[]; width?: number; height?: number; label: string }) {
  const max = Math.max(1, ...values);
  const n = values.length;
  const x = (i: number) => (n <= 1 ? width / 2 : 2 + (i / (n - 1)) * (width - 6));
  const y = (v: number) => height - 3 - (v / max) * (height - 7);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = values[n - 1] ?? 0;
  return (
    <svg width={width} height={height} role="img" aria-label={label} data-testid="sparkline" className="block">
      <line x1={0} x2={width} y1={height - 3 + 0.5} y2={height - 3 + 0.5} className={GRID} strokeWidth={1} />
      {n > 0 && <path d={d} fill="none" className="stroke-neutral-400 dark:stroke-neutral-500" strokeWidth={1.5} strokeLinejoin="round" />}
      {n > 0 && <circle cx={x(n - 1)} cy={y(last)} r={2.5} className="fill-accent-500" />}
    </svg>
  );
}

/** Horizontal bars for ranked counts (HTML, so long labels wrap). */
export function RankBars({ rows, testId }: { rows: { key: string; label: ReactNode; value: number; detail?: ReactNode }[]; testId?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="space-y-2.5" data-testid={testId}>
      {rows.map((r) => (
        <li key={r.key} className="text-sm">
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="min-w-0 break-words">{r.label}</span>
            <span className="shrink-0 tabular-nums text-neutral-600 dark:text-neutral-400">{r.value}</span>
          </div>
          <div className="h-2 rounded-full bg-neutral-100 dark:bg-neutral-800">
            <div className="h-2 rounded-full bg-neutral-500 dark:bg-neutral-400" style={{ width: `${(r.value / max) * 100}%` }} />
          </div>
          {r.detail && <div className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{r.detail}</div>}
        </li>
      ))}
    </ul>
  );
}

/** Re-render on an interval (for "x minutes ago" labels). */
export function useTick(ms: number): void {
  const [, set] = useState(0);
  useEffect(() => {
    const t = setInterval(() => set((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
}
