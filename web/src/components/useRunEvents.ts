import { useEffect, useRef, useState } from "react";
import { api, type ForgeEvent } from "../api";

const TYPES = ["run_started", "assistant", "tool_call", "tool_result", "run_finished", "error"];

/** Streams a run's events over SSE, reconnecting from the last seq. Falls back to events.json. */
export function useRunEvents(runId: string | undefined, share?: string) {
  const [events, setEvents] = useState<ForgeEvent[]>([]);
  const [done, setDone] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const lastSeq = useRef(-1);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    setDone(false);
    setStreamError(null);
    lastSeq.current = -1;
    let es: EventSource | null = null;
    let closed = false;
    let failures = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const add = (ev: ForgeEvent) => {
      if (ev.seq <= lastSeq.current) return;
      lastSeq.current = ev.seq;
      setEvents((xs) => [...xs, ev]);
      if (ev.type === "run_finished") setDone(true);
    };

    const fallback = async () => {
      try {
        const all = await api.getEvents(runId, share);
        all.sort((a, b) => a.seq - b.seq).forEach(add);
        if (all.some((e) => e.type === "run_finished")) {
          setDone(true);
          return true;
        }
      } catch (e) {
        setStreamError((e as Error).message);
      }
      return false;
    };

    const open = () => {
      if (closed) return;
      es = new EventSource(api.eventsUrl(runId, lastSeq.current >= 0 ? lastSeq.current : undefined, share));
      const onMsg = (m: MessageEvent) => {
        failures = 0;
        try {
          add(JSON.parse(m.data) as ForgeEvent);
        } catch {
          /* ignore malformed */
        }
      };
      TYPES.forEach((t) => es!.addEventListener(t, onMsg as EventListener));
      es.onmessage = onMsg;
      es.addEventListener("end", () => {
        setDone(true);
        closed = true;
        es?.close();
      });
      es.onerror = async () => {
        es?.close();
        if (closed) return;
        failures += 1;
        if (failures >= 3) {
          const finished = await fallback();
          if (finished || closed) return;
        }
        retry = setTimeout(open, Math.min(1000 * failures, 5000));
      };
    };
    open();

    return () => {
      closed = true;
      es?.close();
      if (retry) clearTimeout(retry);
    };
  }, [runId, share]);

  return { events, done, streamError };
}
