import { useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, Play, Terminal, Wrench } from "lucide-react";
import type { ForgeEvent } from "../api";

interface ToolPair {
  call: ForgeEvent;
  result?: ForgeEvent;
}

type Item =
  | { kind: "event"; ev: ForgeEvent }
  | { kind: "tool"; pair: ToolPair };

function str(v: unknown): string {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

export function group(events: ForgeEvent[]): Item[] {
  const items: Item[] = [];
  const byId = new Map<string, ToolPair>();
  for (const ev of events) {
    if (ev.type === "tool_call") {
      const pair: ToolPair = { call: ev };
      byId.set(str(ev.payload.id), pair);
      items.push({ kind: "tool", pair });
    } else if (ev.type === "tool_result") {
      const pair = byId.get(str(ev.payload.id));
      if (pair && !pair.result) pair.result = ev;
      else items.push({ kind: "event", ev });
    } else {
      items.push({ kind: "event", ev });
    }
  }
  return items;
}

/** The bash tool's argument is `cmd` (forge/loop.py); accept `command` too for older traces. */
export function bashCommand(args: Record<string, unknown>): string {
  if (typeof args.cmd === "string") return args.cmd;
  if (typeof args.command === "string") return args.command;
  return "";
}

export function argsPreview(name: string, args: Record<string, unknown>): string {
  if (name === "bash" && bashCommand(args)) return bashCommand(args);
  if (typeof args.path === "string") return args.path;
  const s = JSON.stringify(args);
  return s.length > 80 ? s.slice(0, 80) + "..." : s;
}

function ToolCard({ pair, simple }: { pair: ToolPair; simple?: boolean }) {
  const [open, setOpen] = useState(false);
  const name = str(pair.call.payload.name);
  const args = (pair.call.payload.args as Record<string, unknown>) || {};
  const isErr = Boolean(pair.result?.payload.is_error);
  const output = str(pair.result?.payload.output);
  return (
    <div
      data-testid="tool-card"
      data-tool={name}
      data-error={isErr ? "true" : undefined}
      className={`rounded-lg border ${
        isErr ? "border-red-200 dark:border-red-900" : "border-neutral-200 dark:border-neutral-800"
      } bg-white dark:bg-neutral-900`}
    >
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-4 w-4 shrink-0 text-neutral-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-neutral-400" />}
        {name === "bash" ? <Terminal className="h-4 w-4 shrink-0 text-neutral-500" /> : <Wrench className="h-4 w-4 shrink-0 text-neutral-500" />}
        <span className="font-medium">{name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-neutral-500 dark:text-neutral-400">
          {argsPreview(name, args)}
        </span>
        {!pair.result && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-blue-500" />}
        {isErr && <span className="shrink-0 text-xs font-medium text-red-600 dark:text-red-400">error</span>}
      </button>
      {open && (
        <div className="space-y-2 border-t border-neutral-200 p-3 dark:border-neutral-800">
          {!simple && (
            <div>
              <div className="mb-1 text-xs font-medium text-neutral-500">Arguments</div>
              <pre className="mono-block">{JSON.stringify(args, null, 2)}</pre>
            </div>
          )}
          {simple && name === "bash" && <pre className="mono-block">{bashCommand(args)}</pre>}
          {pair.result && (
            <div>
              <div className="mb-1 text-xs font-medium text-neutral-500">Output</div>
              <pre
                className={`mono-block max-h-96 ${
                  isErr ? "bg-red-50 text-red-800 dark:bg-red-950/50 dark:text-red-300" : ""
                }`}
              >
                {output || "(no output)"}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      <div className="z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        {icon}
      </div>
      <div className="min-w-0 flex-1 pt-0.5">{children}</div>
    </li>
  );
}

export default function TraceTimeline({
  events,
  live,
  simple,
}: {
  events: ForgeEvent[];
  live?: boolean;
  simple?: boolean;
}) {
  const items = group(events);
  if (items.length === 0) {
    return (
      <div className="py-6 text-center text-sm text-neutral-500 dark:text-neutral-400">
        {live ? "Waiting for the agent to start..." : "No trace events recorded."}
      </div>
    );
  }
  return (
    <ol className="relative">
      <span className="absolute bottom-3 left-3.5 top-3 w-px bg-neutral-200 dark:bg-neutral-800" aria-hidden="true" />
      {items.map((it) => {
        if (it.kind === "tool") {
          return (
            <Row key={it.pair.call.seq} icon={<Wrench className="h-3.5 w-3.5 text-neutral-500" />}>
              <ToolCard pair={it.pair} simple={simple} />
            </Row>
          );
        }
        const ev = it.ev;
        const p = ev.payload;
        switch (ev.type) {
          case "run_started":
            if (simple) return null;
            return (
              <Row key={ev.seq} icon={<Play className="h-3.5 w-3.5 text-neutral-500" />}>
                <div className="text-sm text-neutral-500 dark:text-neutral-400">
                  Run started with <span className="font-mono text-xs">{str(p.model)}</span>
                </div>
              </Row>
            );
          case "assistant":
            return (
              <Row key={ev.seq} icon={<Bot className="h-3.5 w-3.5 text-accent-600 dark:text-accent-400" />}>
                <div className="whitespace-pre-wrap break-words rounded-lg rounded-tl-sm bg-accent-50 px-3.5 py-2.5 text-sm leading-relaxed text-neutral-800 dark:bg-accent-900/25 dark:text-neutral-200">
                  {str(p.text)}
                </div>
              </Row>
            );
          case "tool_result":
            return (
              <Row key={ev.seq} icon={<Terminal className="h-3.5 w-3.5 text-neutral-500" />}>
                <pre className={`mono-block ${p.is_error ? "bg-red-50 text-red-800 dark:bg-red-950/50 dark:text-red-300" : ""}`}>
                  {str(p.output)}
                </pre>
              </Row>
            );
          case "error":
            return (
              <Row key={ev.seq} icon={<AlertTriangle className="h-3.5 w-3.5 text-red-500" />}>
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                  {str(p.message)}
                </div>
              </Row>
            );
          case "run_finished": {
            const ok = p.status === "succeeded";
            return (
              <Row
                key={ev.seq}
                icon={
                  ok ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> : <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                }
              >
                <div className="text-sm text-neutral-600 dark:text-neutral-400">
                  Finished <span className="font-medium">{str(p.status)}</span>
                  {!simple && (
                    <>
                      {" "}in {str(p.turns)} turns, {Number(p.tokens_in || 0).toLocaleString()} in /{" "}
                      {Number(p.tokens_out || 0).toLocaleString()} out tokens
                    </>
                  )}
                </div>
              </Row>
            );
          }
          default:
            return null;
        }
      })}
      {live && (
        <li className="relative flex items-center gap-3 pt-1">
          <div className="z-10 flex h-7 w-7 items-center justify-center">
            <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          </div>
          <span className="text-xs text-neutral-500">Working...</span>
        </li>
      )}
    </ol>
  );
}
