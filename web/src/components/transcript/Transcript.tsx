import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ArrowDown } from "lucide-react";
import type { ForgeEvent, Run } from "../../api";
import Markdown from "./Markdown";
import { Dot, ExpandContext, ToolRow } from "./tools";
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

/** Current time in unix seconds, ticking once a second while `active`. */
export function useNow(active: boolean): number {
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

function atBottom(el: HTMLElement | null): boolean {
  if (el) return el.scrollTop + el.clientHeight >= el.scrollHeight - NEAR_BOTTOM_PX;
  const doc = document.documentElement;
  return window.innerHeight + window.scrollY >= doc.scrollHeight - NEAR_BOTTOM_PX;
}

/**
 * Follow the newest step while the run is live, unless the reader scrolled up; then offer a pill.
 * `key` changes whenever new content lands. Scrolls the window, or `container` when given.
 */
export function useAutoScroll(key: unknown, live: boolean, container?: RefObject<HTMLElement>) {
  const endRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  useEffect(() => {
    const target: HTMLElement | Window = container?.current ?? window;
    const onScroll = () => setPinned(atBottom(container?.current ?? null));
    target.addEventListener("scroll", onScroll, { passive: true });
    return () => target.removeEventListener("scroll", onScroll);
  }, [container]);

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

/** Expand all / collapse all state plus the Ctrl+O shortcut (one per page). */
export function useExpandAll() {
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
  return { expand, toggleAll };
}

export function ExpandAllButton({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button type="button" className="btn-ghost btn-sm" onClick={onClick} data-testid="expand-all">
      {open ? "Collapse all" : "Expand all"}
      <kbd className="tx-kbd">Ctrl+O</kbd>
    </button>
  );
}

export function StatusLine({
  events,
  run,
  live,
  now,
  hint = "ctrl+o to expand",
}: {
  events: ForgeEvent[];
  run?: Run | null;
  live: boolean;
  now: number;
  hint?: string;
}) {
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
          {hint}
        </span>
      </div>
    );
  }
  const status = str(fin?.status) || run?.status || "";
  const end = run?.ended_at ?? events[events.length - 1]?.ts ?? start;
  const turns = typeof fin?.turns === "number" ? fin.turns : run?.turns ?? 0;
  const cost = costFrom(events, run as unknown as Record<string, unknown>);
  const verb =
    status === "succeeded"
      ? "Done in"
      : status === "timeout"
        ? "Timed out after"
        : status === "cancelled"
          ? "Stopped after"
          : "Failed after";
  const parts = [`${verb} ${fmtSeconds(Math.max(0, end - start))}`, plural(turns, "turn")];
  if (tokens) parts.push(`${fmtTokens(tokens)} tokens`);
  if (cost !== null) parts.push(fmtCost(cost));
  const ok = status === "succeeded";
  const muted = status === "cancelled";
  return (
    <div className="tx-status" data-testid="status-line" data-state={status || "done"}>
      <span className={ok ? "tx-star" : muted ? "text-neutral-400" : "text-red-600 dark:text-red-400"} aria-hidden="true">
        ✻
      </span>{" "}
      <span
        className={
          ok || muted ? "text-neutral-700 dark:text-neutral-300" : "text-red-700 dark:text-red-400"
        }
      >
        {parts.join(" · ")}
      </span>
    </div>
  );
}

export function PromptLine({ input }: { input: string }) {
  return (
    <div className="tx-prompt" data-testid="transcript-input">
      <span className="tx-caret" aria-hidden="true">
        &gt;
      </span>
      <p className="min-w-0 flex-1 whitespace-pre-wrap break-words">{input}</p>
    </div>
  );
}

/** The rows of one run: optional `>` prompt, narration, tool steps, errors, final answer. */
export function TranscriptBody({
  events,
  run,
  live,
  input,
  now,
}: {
  events: ForgeEvent[];
  run?: Run | null;
  live: boolean;
  input?: string;
  now: number;
}) {
  const items = useMemo(() => buildItems(events), [events]);
  const hasErrorEvent = items.some((i) => i.kind === "error");
  return (
    <>
      {input && <PromptLine input={input} />}
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
    </>
  );
}

/** Changes whenever new content lands in `events` (a new item, or a pending tool getting its result). */
export function scrollKeyFor(events: ForgeEvent[]): string {
  const last = events[events.length - 1];
  return `${events.length}:${last?.seq ?? -1}`;
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
  const now = useNow(live);
  const { expand, toggleAll } = useExpandAll();
  const { endRef, showJump, jump } = useAutoScroll(scrollKeyFor(events), live);

  return (
    <ExpandContext.Provider value={expand}>
      <div className="tx" data-testid="transcript">
        <div className="tx-toolbar">
          <ExpandAllButton open={expand.open} onClick={toggleAll} />
        </div>
        <div className="tx-body">
          <TranscriptBody events={events} run={run} live={live} input={input} now={now} />
          <div ref={endRef} />
        </div>
        <div className={`tx-statusbar ${live ? "tx-statusbar-live" : ""}`}>
          {showJump && <JumpPill onClick={jump} />}
          <StatusLine events={events} run={run} live={live} now={now} />
        </div>
      </div>
    </ExpandContext.Provider>
  );
}

export function JumpPill({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="tx-jump" onClick={onClick} data-testid="jump-latest">
      <ArrowDown className="h-3.5 w-3.5" /> Jump to latest
    </button>
  );
}

export function ErrorBlock({ message }: { message: string }) {
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
