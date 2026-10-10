import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import type { ForgeEvent, Run } from "../../api";
import Markdown from "./Markdown";
import { Dot, ExpandContext, ToolBlock } from "./tools";
import {
  buildItems,
  costFrom,
  fmtCost,
  fmtElapsed,
  fmtSeconds,
  fmtTokens,
  plural,
  str,
  tokensFromEvents,
} from "./model";
import "./transcript.css";

const ToolRow = memo(ToolBlock);

/** Current time in unix seconds, ticking once a second while `active`. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now() / 1000);
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

const NEAR_BOTTOM_PX = 64;

function atBottom(): boolean {
  const el = document.documentElement;
  return window.innerHeight + window.scrollY >= el.scrollHeight - NEAR_BOTTOM_PX;
}

/**
 * Follow the newest step while the run is live, unless the reader scrolled up; then offer a pill.
 * `key` changes whenever new content lands.
 */
export function useAutoScroll(key: unknown, live: boolean) {
  const endRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  useEffect(() => {
    const onScroll = () => setPinned(atBottom());
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const jump = useCallback(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
    setPinned(true);
  }, []);

  useLayoutEffect(() => {
    if (live && pinned) endRef.current?.scrollIntoView?.({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, live]);

  return { endRef, showJump: live && !pinned, jump };
}

function StatusLine({ events, run, live, now }: { events: ForgeEvent[]; run?: Run | null; live: boolean; now: number }) {
  const fin = events.find((e) => e.type === "run_finished")?.payload;
  const start = run?.started_at ?? events[0]?.ts ?? now;
  const tokens = tokensFromEvents(events) || (run ? run.tokens_in + run.tokens_out : 0);
  if (live) {
    const parts = [fmtElapsed(Math.max(0, now - start))];
    if (tokens) parts.push(`${fmtTokens(tokens)} tokens`);
    return (
      <div className="tx-status" data-testid="status-line" data-state="running">
        <span className="tx-star animate-pulse" aria-hidden="true">
          ✻
        </span>{" "}
        <span className="font-medium text-neutral-800 dark:text-neutral-200">Working…</span>{" "}
        <span className="text-neutral-500 dark:text-neutral-400">{parts.join(" · ")}</span>
        <span className="ml-auto hidden text-neutral-400 sm:inline" aria-hidden="true">
          ctrl+o to expand
        </span>
      </div>
    );
  }
  const status = str(fin?.status) || run?.status || "";
  const end = run?.ended_at ?? events[events.length - 1]?.ts ?? start;
  const turns = typeof fin?.turns === "number" ? fin.turns : run?.turns ?? 0;
  const cost = costFrom(events, run as unknown as Record<string, unknown>);
  const verb = status === "succeeded" ? "Done in" : status === "timeout" ? "Timed out after" : "Failed after";
  const parts = [`${verb} ${fmtSeconds(Math.max(0, end - start))}`, plural(turns, "turn")];
  if (tokens) parts.push(`${fmtTokens(tokens)} tokens`);
  if (cost !== null) parts.push(fmtCost(cost));
  const ok = status === "succeeded";
  return (
    <div className="tx-status" data-testid="status-line" data-state={status || "done"}>
      <span className={ok ? "tx-star" : "text-red-600 dark:text-red-400"} aria-hidden="true">
        ✻
      </span>{" "}
      <span className={ok ? "text-neutral-700 dark:text-neutral-300" : "text-red-700 dark:text-red-400"}>
        {parts.join(" · ")}
      </span>
    </div>
  );
}

export interface TranscriptProps {
  events: ForgeEvent[];
  run?: Run | null;
  live: boolean;
  /** Shown as the opening `>` prompt line. */
  input?: string;
}

/** Claude Code style run transcript: narration, tool calls with `⎿` results, a status line. */
export default function Transcript({ events, run, live, input }: TranscriptProps) {
  const items = useMemo(() => buildItems(events), [events]);
  const now = useNow(live);
  const [expand, setExpand] = useState({ open: false, epoch: 0 });
  const toggleAll = useCallback(() => setExpand((e) => ({ open: !e.open, epoch: e.epoch + 1 })), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "o" || e.key === "O")) {
        e.preventDefault();
        toggleAll();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleAll]);

  const lastItem = items[items.length - 1];
  const scrollKey = `${items.length}:${lastItem?.kind === "tool" && lastItem.result ? 1 : 0}`;
  const { endRef, showJump, jump } = useAutoScroll(scrollKey, live);
  const hasErrorEvent = items.some((i) => i.kind === "error");

  return (
    <ExpandContext.Provider value={expand}>
      <div className="tx" data-testid="transcript">
        <div className="tx-toolbar">
          <button type="button" className="btn-ghost btn-sm" onClick={toggleAll} data-testid="expand-all">
            {expand.open ? "Collapse all" : "Expand all"}
            <kbd className="tx-kbd">Ctrl+O</kbd>
          </button>
        </div>
        <div className="tx-body">
          {input && (
            <div className="tx-prompt" data-testid="transcript-input">
              <span className="tx-caret" aria-hidden="true">
                &gt;
              </span>
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">{input}</p>
            </div>
          )}
          {items.length === 0 && (
            <div className="tx-row text-neutral-500">
              <Dot tone="pending" />
              <span>{live ? "Waiting for the agent to start…" : "No trace events recorded."}</span>
            </div>
          )}
          {items.map((it) => {
            switch (it.kind) {
              case "narration":
                return (
                  <div
                    key={it.seq}
                    className={`tx-row ${it.final ? "tx-final" : ""}`}
                    data-testid="narration"
                    data-kind={it.final ? "final" : "progress"}
                  >
                    <Dot tone="text" />
                    <div className="min-w-0 flex-1 text-neutral-900 dark:text-neutral-100">
                      <Markdown text={it.text} />
                    </div>
                  </div>
                );
              case "tool":
                return <ToolRow key={it.seq} call={it.call} result={it.result} now={it.result ? 0 : now} />;
              case "error":
                return <ErrorBlock key={it.seq} message={it.message} />;
              case "orphan":
                return (
                  <div key={it.seq} className="tx-row">
                    <Dot tone={it.ev.payload.is_error ? "error" : "ok"} />
                    <pre className="tx-pre min-w-0 flex-1">{str(it.ev.payload.output)}</pre>
                  </div>
                );
            }
          })}
          {!live && run?.error && !hasErrorEvent && <ErrorBlock message={run.error} />}
          <div ref={endRef} />
        </div>
        <div className={`tx-statusbar ${live ? "tx-statusbar-live" : ""}`}>
          {showJump && (
            <button type="button" className="tx-jump" onClick={jump} data-testid="jump-latest">
              <ArrowDown className="h-3.5 w-3.5" /> Jump to latest
            </button>
          )}
          <StatusLine events={events} run={run} live={live} now={now} />
        </div>
      </div>
    </ExpandContext.Provider>
  );
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="tx-row" data-testid="transcript-error">
      <Dot tone="error" />
      <div className="tx-error min-w-0 flex-1">
        <div className="font-semibold">Error</div>
        <pre className="tx-pre">{message}</pre>
      </div>
    </div>
  );
}
