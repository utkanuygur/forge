import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { api, errMsg, type Timeline } from "../api";
import { fmtMs, fmtUsd } from "../lib/format";
import { layoutTimeline, timeShares } from "../lib/timeline";
import { SWATCH_BG, useWidth } from "./charts/Charts";

const MAX_ROWS = 40;

/** Waterfall of a run's model turns and tool calls on one time axis, with a model vs tool summary. */
export default function StepTimeline({ runId, refreshKey }: { runId: string; refreshKey?: unknown }) {
  const [tl, setTl] = useState<Timeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [ref, width] = useWidth<HTMLDivElement>();

  useEffect(() => {
    let alive = true;
    api
      .timeline(runId)
      .then((t) => alive && (setTl(t), setError(null)))
      .catch((e) => alive && setError(errMsg(e)));
    return () => {
      alive = false;
    };
  }, [runId, refreshKey]);

  // The measured container is always mounted, so the width is known before the steps render.
  let message: string | null = null;
  if (error) message = `Timeline unavailable: ${error}`;
  else if (!tl) message = "Loading timeline...";
  else if (tl.spans.length === 0) message = "No model turns or tool calls recorded yet.";
  if (message || !tl)
    return (
      <div ref={ref}>
        <p className="text-sm text-neutral-500">{message}</p>
      </div>
    );

  const { rows, ticks, axisMs } = layoutTimeline(tl.spans, tl.total_ms, width < 480 ? 3 : 5);
  const shown = all ? rows : rows.slice(0, MAX_ROWS);
  const share = timeShares(tl.model_ms, tl.tool_ms, tl.total_ms);
  const narrow = width < 560;

  return (
    <div ref={ref} className="space-y-4" data-testid="step-timeline" data-estimated={tl.estimated}>
      <div>
        <div className="mb-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
          <span className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${SWATCH_BG.model}`} aria-hidden /> Model{" "}
            <span className="font-medium tabular-nums" data-testid="model-time">{fmtMs(tl.model_ms)}</span>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${SWATCH_BG.tool}`} aria-hidden /> Tools{" "}
            <span className="font-medium tabular-nums" data-testid="tool-time">{fmtMs(tl.tool_ms)}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 text-neutral-500 dark:text-neutral-400">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-neutral-300 dark:bg-neutral-600" aria-hidden /> Other{" "}
            <span className="tabular-nums">{fmtMs(tl.other_ms)}</span>
          </span>
          <span className="text-neutral-500 dark:text-neutral-400">
            Total <span className="tabular-nums">{fmtMs(tl.total_ms)}</span>
            {tl.queue_ms !== null && <> after {fmtMs(tl.queue_ms)} sandbox start</>}
          </span>
        </div>
        <div className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full" aria-label="Model time versus tool time" role="img">
          {share.model > 0 && <div className={SWATCH_BG.model} style={{ width: `${share.model * 100}%` }} />}
          {share.tool > 0 && <div className={SWATCH_BG.tool} style={{ width: `${share.tool * 100}%` }} />}
          {share.other > 0 && <div className="bg-neutral-200 dark:bg-neutral-700" style={{ width: `${share.other * 100}%` }} />}
        </div>
        {tl.estimated && (
          <p className="mt-2 text-xs text-neutral-500">This run was recorded before step timing existed, so durations are estimated from event timestamps.</p>
        )}
      </div>

      <div className="text-xs">
        <div className={`grid ${narrow ? "grid-cols-1" : "grid-cols-[minmax(0,13rem)_minmax(0,1fr)]"} gap-x-3`}>
          {!narrow && <div />}
          <div className="relative mb-1 h-4 text-neutral-500 dark:text-neutral-400" aria-hidden>
            {ticks.map((t, i) => (
              <span
                key={t}
                className="absolute whitespace-nowrap tabular-nums"
                style={{ left: `${(t / axisMs) * 100}%`, transform: i === 0 ? "none" : i === ticks.length - 1 ? "translateX(-100%)" : "translateX(-50%)" }}
              >
                {fmtMs(t).replace("<1 ms", "0")}
              </span>
            ))}
          </div>
        </div>
        <ol className="space-y-1" aria-label="Steps">
          {shown.map((s) => (
            <li
              key={`${s.kind}-${s.seq}`}
              data-testid="timeline-step"
              data-kind={s.kind}
              data-label={s.label}
              className={`grid ${narrow ? "grid-cols-1 gap-y-0.5" : "grid-cols-[minmax(0,13rem)_minmax(0,1fr)] items-center"} gap-x-3`}
            >
              <div className="flex min-w-0 items-baseline gap-1.5">
                {s.is_error && <AlertTriangle className="h-3.5 w-3.5 shrink-0 self-center text-red-600" aria-label="error" />}
                <span className={`truncate font-medium ${s.kind === "tool" ? "font-mono" : ""}`}>{s.label}</span>
                {s.summary && <span className="truncate font-mono text-neutral-500">{s.summary}</span>}
                <span className="ml-auto shrink-0 tabular-nums text-neutral-600 dark:text-neutral-400">
                  {fmtMs(s.duration_ms)}
                  {s.kind === "model" && s.tokens_in !== null && s.tokens_in !== undefined && (
                    <span className="text-neutral-400"> · {s.tokens_in}/{s.tokens_out} tok</span>
                  )}
                </span>
              </div>
              <div className="relative h-4 rounded bg-neutral-100 dark:bg-neutral-800/70" title={`${s.label}: ${fmtMs(s.duration_ms)} starting at ${fmtMs(s.start_ms)}`}>
                <div
                  className={`absolute inset-y-0.5 rounded-sm ${s.kind === "model" ? SWATCH_BG.model : SWATCH_BG.tool} ${s.is_error ? "ring-2 ring-red-500 ring-offset-1 ring-offset-white dark:ring-offset-neutral-900" : ""}`}
                  style={{ left: `${s.left}%`, width: `${s.width}%` }}
                />
              </div>
            </li>
          ))}
        </ol>
        {rows.length > MAX_ROWS && (
          <button type="button" className="btn-ghost btn-sm mt-2" onClick={() => setAll((x) => !x)}>
            {all ? "Show fewer steps" : `Show all ${rows.length} steps`}
          </button>
        )}
      </div>
      <p className="text-xs text-neutral-500 dark:text-neutral-400" data-testid="run-cost">
        Cost {tl.cost_usd === null ? "unknown (unknown price)" : fmtUsd(tl.cost_usd)}
        {tl.cost_note && tl.cost_usd !== null && ` (${tl.cost_note})`} for {tl.tokens_in.toLocaleString()} input and{" "}
        {tl.tokens_out.toLocaleString()} output tokens
        {(tl.cache_read_tokens || tl.cache_write_tokens)
          ? ` (plus ${(tl.cache_read_tokens ?? 0).toLocaleString()} cache read and ${(tl.cache_write_tokens ?? 0).toLocaleString()} cache write tokens, not priced)`
          : ""}
      </p>
    </div>
  );
}
