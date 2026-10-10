import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useRunEvents } from "../../src/components/useRunEvents";
import type { ForgeEvent } from "../../src/api";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((m: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((m: MessageEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    const m = { data: typeof data === "string" ? data : JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners.get(type) || []) fn(m);
  }
  fail() {
    this.onerror?.();
  }
  static last() {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

const ev = (seq: number, type: ForgeEvent["type"] = "assistant"): ForgeEvent => ({
  run_id: "r_1",
  seq,
  ts: 0,
  type,
  payload: { text: `e${seq}` },
});

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useRunEvents", () => {
  it("opens the SSE stream for the run (with share token) and dedupes by seq", () => {
    const { result } = renderHook(() => useRunEvents("r_1", "tok"));
    const es = MockEventSource.last();
    expect(es.url).toBe("/v1/runs/r_1/events?share=tok");
    act(() => {
      es.emit("run_started", ev(0, "run_started"));
      es.emit("assistant", ev(1));
      es.emit("assistant", ev(1)); // duplicate
      es.emit("assistant", ev(0)); // older
      es.emit("tool_call", ev(2, "tool_call"));
      es.emit("assistant", "not json"); // ignored
    });
    expect(result.current.events.map((e) => e.seq)).toEqual([0, 1, 2]);
    expect(result.current.done).toBe(false);
  });

  it("marks done on run_finished and on the end event, and closes the stream", () => {
    const { result } = renderHook(() => useRunEvents("r_1"));
    const es = MockEventSource.last();
    act(() => es.emit("run_finished", ev(0, "run_finished")));
    expect(result.current.done).toBe(true);
    act(() => es.emit("end", ""));
    expect(es.closed).toBe(true);
    // An error after end must not reconnect.
    act(() => es.fail());
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("reconnects after an error, resuming from the last seq", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRunEvents("r_1"));
    const first = MockEventSource.last();
    act(() => {
      first.emit("assistant", ev(0));
      first.emit("assistant", ev(1));
      first.fail();
    });
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1000));
    const second = MockEventSource.last();
    expect(MockEventSource.instances).toHaveLength(2);
    expect(second.url).toBe("/v1/runs/r_1/events?after=1");
    // The server replays seq 1 after reconnect; it must not duplicate.
    act(() => {
      second.emit("assistant", ev(1));
      second.emit("assistant", ev(2));
    });
    expect(result.current.events.map((e) => e.seq)).toEqual([0, 1, 2]);
  });

  it("falls back to events.json after three consecutive failures", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify([ev(2, "run_finished"), ev(0), ev(1)]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useRunEvents("r_1"));
    act(() => MockEventSource.last().fail());
    act(() => vi.advanceTimersByTime(1000));
    act(() => MockEventSource.last().fail());
    act(() => vi.advanceTimersByTime(2000));
    expect(MockEventSource.instances).toHaveLength(3);
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => {
      MockEventSource.last().fail();
    });
    vi.useRealTimers();
    await waitFor(() => expect(result.current.done).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith("/v1/runs/r_1/events.json", expect.objectContaining({ method: "GET" }));
    expect(result.current.events.map((e) => e.seq)).toEqual([0, 1, 2]);
    // Finished: no further reconnect attempts.
    await new Promise((r) => setTimeout(r, 50));
    expect(MockEventSource.instances).toHaveLength(3);
  });

  it("reports a stream error when the fallback fails and keeps retrying", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "run not found" }), { status: 404, headers: { "content-type": "application/json" } })),
    );
    const { result } = renderHook(() => useRunEvents("r_1"));
    act(() => MockEventSource.last().fail());
    act(() => vi.advanceTimersByTime(1000));
    act(() => MockEventSource.last().fail());
    act(() => vi.advanceTimersByTime(2000));
    await act(async () => {
      MockEventSource.last().fail();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.streamError).toBe("run not found");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(MockEventSource.instances).toHaveLength(4);
  });

  it("closes the stream on unmount and does nothing without a run id", () => {
    const { unmount } = renderHook(() => useRunEvents("r_1"));
    const es = MockEventSource.last();
    unmount();
    expect(es.closed).toBe(true);
    renderHook(() => useRunEvents(undefined));
    expect(MockEventSource.instances).toHaveLength(1);
  });
});
