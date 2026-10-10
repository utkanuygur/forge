import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { bands, labelIndexes, linear, niceStep, niceTicks } from "../../src/lib/scale";
import { fmtDelta, fmtMs, fmtUsd } from "../../src/lib/format";
import { buildRunQuery, hasActiveFilters } from "../../src/lib/runFilters";
import { layoutTimeline, MIN_WIDTH_PCT, timeShares, type TimelineSpan } from "../../src/lib/timeline";
import { PERIOD_KEY, readPeriod, writePeriod } from "../../src/lib/period";
import { EmptyChart, StackedBarChart, STATUS_KEYS, topRoundedRect } from "../../src/components/charts/Charts";

afterEach(() => vi.unstubAllGlobals());

describe("OBS1 chart scales and axis ticks", () => {
  it("chooses nice steps of 1, 2, 2.5 or 5 times a power of ten", () => {
    expect(niceStep(7, 4)).toBe(2);
    expect(niceStep(0.13, 4)).toBe(0.05);
    expect(niceStep(950, 4)).toBe(250);
  });

  it("ticks start at zero and cover the maximum", () => {
    expect(niceTicks(7, 4)).toEqual([0, 2, 4, 6, 8]);
    expect(niceTicks(4, 4, true)).toEqual([0, 1, 2, 3, 4]);
    expect(niceTicks(0.13, 4)).toEqual([0, 0.05, 0.1, 0.15]);
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(1, 4, true)).toEqual([0, 1]);
  });

  it("maps linearly and keeps bars thin inside their slot", () => {
    const y = linear([0, 8], [200, 0]);
    expect(y(0)).toBe(200);
    expect(y(4)).toBe(100);
    const b = bands(10, 600);
    expect(b.slot).toBe(60);
    expect(b.bar).toBe(24); // capped
    expect(b.x(1)).toBe(60 + 18);
  });

  it("labels at most N buckets and always the last one", () => {
    expect(labelIndexes(5, 10)).toEqual([0, 1, 2, 3, 4]);
    const idx = labelIndexes(30, 5);
    expect(idx.length).toBeLessThanOrEqual(5);
    expect(idx[idx.length - 1]).toBe(29);
  });

  it("draws stacked bars to scale from a zero baseline", () => {
    const points = [
      { t: 0, succeeded: 2, failed: 0, timeout: 0, in_progress: 0 },
      { t: 86400, succeeded: 4, failed: 2, timeout: 0, in_progress: 0 },
    ];
    const { container } = render(<StackedBarChart points={points} keys={STATUS_KEYS} bucket="day" bucketSize={86400} height={200} label="runs" />);
    const bars = container.querySelectorAll('[data-testid="bar"]');
    expect([...bars].map((b) => b.getAttribute("data-total"))).toEqual(["2", "6"]);
    // y axis ticks 0..6 in integer steps; the 6-run bar reaches the top tick, the 2-run bar a third of the way.
    const texts = [...container.querySelectorAll("text")].map((t) => t.textContent);
    expect(texts).toContain("6");
    const top = (g: Element) => Math.min(...[...g.querySelectorAll("rect, path")].map((el) => Number(el.getAttribute("y") ?? /Q[\d.]+,([\d.]+)/.exec(el.getAttribute("d") || "")?.[1] ?? 0)));
    const baseline = 200 - 24;
    const plotTop = 18;
    const h2 = baseline - top(bars[0]);
    const h6 = baseline - top(bars[1]);
    expect(h6).toBeCloseTo(baseline - plotTop, 0);
    expect(h2 / h6).toBeGreaterThan(0.28);
    expect(h2 / h6).toBeLessThan(0.4);
  });

  it("rounds only the data end of a bar", () => {
    expect(topRoundedRect(0, 10, 20, 50)).toBe("M0,60V14Q0,10 4,10H16Q20,10 20,14V60Z");
  });

  it("shows a designed empty state", () => {
    const { getByTestId } = render(<EmptyChart />);
    expect(getByTestId("chart-empty").textContent).toContain("No runs in this period yet");
  });
});

describe("OBS1 KPI delta formatting", () => {
  const d = (value: number | null, previous: number | null) => ({
    value,
    previous,
    change: value !== null && previous !== null ? value - previous : null,
    change_pct: value !== null && previous ? (value - previous) / Math.abs(previous) : null,
  });

  it("rates change in percentage points with a direction aware tone", () => {
    expect(fmtDelta(d(0.9, 0.8), "rate", "up")).toEqual({ text: "+10.0 pts", tone: "good" });
    expect(fmtDelta(d(0.7, 0.8), "rate", "up")).toEqual({ text: "−10.0 pts", tone: "bad" });
  });

  it("durations and counts change in percent; lower duration is good", () => {
    expect(fmtDelta(d(800, 1000), "duration", "down")).toEqual({ text: "−20%", tone: "good" });
    expect(fmtDelta(d(15, 10), "count", "none")).toEqual({ text: "+50%", tone: "neutral" });
    expect(fmtDelta(d(10.05, 10), "count", "none").text).toBe("+0.5%");
  });

  it("falls back to the absolute change without a previous base, and says when nothing changed", () => {
    expect(fmtDelta(d(28, 0), "count", "none").text).toBe("+28");
    expect(fmtDelta(d(0.64, 0), "usd", "none").text).toBe("+$0.64");
    expect(fmtDelta(d(5, 5), "count", "none").text).toBe("no change");
    expect(fmtDelta(d(1.00001, 1), "usd", "down").text).toBe("no change");
    expect(fmtDelta(d(0.5, null), "rate", "up")).toEqual({ text: "no prior data", tone: "neutral" });
  });

  it("formats durations and money for tiles", () => {
    expect(fmtMs(0.4)).toBe("<1 ms");
    expect(fmtMs(458)).toBe("458 ms");
    expect(fmtMs(2800)).toBe("2.8 s");
    expect(fmtMs(125_000)).toBe("2m 5s");
    expect(fmtUsd(null)).toBe("unknown");
    expect(fmtUsd(0.0315)).toBe("$0.03");
    expect(fmtUsd(0.0004)).toBe("$0.0004");
  });
});

describe("OBS5 run filter query building", () => {
  it("includes only set filters, in a stable order", () => {
    expect(buildRunQuery({})).toBe("");
    expect(
      buildRunQuery({ status: "failed,timeout", version: "2", rating: "down", toolErrors: true, q: "  refund bug ", sort: "duration", limit: 50 }),
    ).toBe("has_tool_errors=true&limit=50&q=refund%20bug&rating=down&sort=duration&status=failed%2Ctimeout&version=2");
  });

  it("omits defaults (newest first, no offset) and knows when filters are active", () => {
    expect(buildRunQuery({ sort: "created_at", order: "desc", offset: 0, toolErrors: false, rating: "" })).toBe("");
    expect(hasActiveFilters({ sort: "cost" })).toBe(false);
    expect(hasActiveFilters({ q: "x" })).toBe(true);
    expect(buildRunQuery({ agent: "a b", source: "share" })).toBe("agent=a%20b&source=share");
  });
});

describe("OBS3 step timeline layout", () => {
  const span = (kind: "model" | "tool", start: number, dur: number, seq: number): TimelineSpan => ({
    kind, label: kind, turn: 1, seq, start_ms: start, duration_ms: dur, is_error: false, estimated: false,
  });

  it("places spans on a shared nice axis", () => {
    const { rows, ticks, axisMs } = layoutTimeline([span("model", 0, 500, 1), span("tool", 500, 100, 2), span("model", 600, 1000, 3)], 1700);
    expect(ticks).toEqual([0, 500, 1000, 1500, 2000]);
    expect(axisMs).toBe(2000);
    expect(rows.map((r) => [r.left, r.width])).toEqual([[0, 25], [25, 5], [30, 50]]);
  });

  it("gives instant steps a minimum visible width and keeps them inside the axis", () => {
    const { rows } = layoutTimeline([span("model", 1000, 0, 1)], 1000);
    expect(rows[0].width).toBe(MIN_WIDTH_PCT);
    expect(rows[0].left + rows[0].width).toBeLessThanOrEqual(100);
  });

  it("splits total time into model, tool and other", () => {
    expect(timeShares(500, 250, 1000)).toEqual({ model: 0.5, tool: 0.25, other: 0.25 });
    expect(timeShares(0, 0, 0)).toEqual({ model: 0, tool: 0, other: 1 });
  });
});

describe("OBS1 period selector storage", () => {
  const install = (store: Pick<Storage, "getItem" | "setItem">) =>
    Object.defineProperty(window, "localStorage", { value: store, configurable: true });

  it("remembers the period per browser and ignores unknown values", () => {
    const mem = new Map<string, string>();
    install({ getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => void mem.set(k, String(v)) });
    expect(readPeriod()).toBe(14);
    writePeriod(7);
    expect(mem.get(PERIOD_KEY)).toBe("7");
    expect(readPeriod()).toBe(7);
    mem.set(PERIOD_KEY, "999");
    expect(readPeriod()).toBe(14);
  });

  it("falls back to the default when storage throws", () => {
    const boom = () => {
      throw new Error("blocked");
    };
    install({ getItem: boom, setItem: boom });
    expect(readPeriod()).toBe(14);
    expect(() => writePeriod(30)).not.toThrow();
  });
});
