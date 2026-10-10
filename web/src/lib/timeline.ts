// Waterfall layout for a run's step timeline (GET /v1/runs/{id}/timeline).
import { niceTicks } from "./scale";

export interface TimelineSpan {
  kind: "model" | "tool" | "approval"; // approval: time paused on a human reviewer (governance)
  label: string;
  turn: number;
  seq: number;
  start_ms: number;
  duration_ms: number;
  tokens_in?: number | null;
  tokens_out?: number | null;
  is_error: boolean;
  estimated: boolean;
  summary?: string;
}

export interface LaidOutSpan extends TimelineSpan {
  /** Left edge and width as a percentage of the axis. */
  left: number;
  width: number;
}

/** Minimum visible bar width in percent, so instant steps still show a mark. */
export const MIN_WIDTH_PCT = 0.6;

/** Places each span on a shared axis from 0 to the nice maximum covering `totalMs`. */
export function layoutTimeline(spans: TimelineSpan[], totalMs: number, tickCount = 4): { rows: LaidOutSpan[]; ticks: number[]; axisMs: number } {
  const end = Math.max(totalMs, ...spans.map((s) => s.start_ms + s.duration_ms), 1);
  const ticks = niceTicks(end, tickCount);
  const axisMs = ticks[ticks.length - 1] || end;
  const rows = spans.map((s) => {
    const left = Math.min(100, (s.start_ms / axisMs) * 100);
    const width = Math.max(MIN_WIDTH_PCT, Math.min(100 - left, (s.duration_ms / axisMs) * 100));
    return { ...s, left: Math.min(left, 100 - MIN_WIDTH_PCT), width };
  });
  return { rows, ticks, axisMs };
}

/** Shares of total time for the summary bar: model, tool, approval wait and other (sandbox and bookkeeping). */
export function timeShares(
  modelMs: number,
  toolMs: number,
  totalMs: number,
  approvalMs = 0,
): { model: number; tool: number; approval: number; other: number } {
  const total = Math.max(totalMs, modelMs + toolMs + approvalMs, 1);
  const model = modelMs / total;
  const tool = toolMs / total;
  const approval = approvalMs / total;
  return { model, tool, approval, other: Math.max(0, 1 - model - tool - approval) };
}
