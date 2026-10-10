import { Check, X } from "lucide-react";
import type { ConfirmRequest, CopilotEvent, CopilotLink } from "../../copilotApi";
import Markdown from "../transcript/Markdown";
import { Dot, Spinner } from "../transcript/tools";

export type Item =
  | { kind: "user"; key: string; text: string }
  | { kind: "text"; key: string; text: string; progress: boolean }
  | {
      kind: "tool";
      key: string;
      id: string;
      tool: string;
      args: Record<string, unknown>;
      ok?: boolean;
      summary?: string;
      links: CopilotLink[];
    }
  | { kind: "confirm"; key: string; req: ConfirmRequest; resolved?: { approved: boolean; by: string } }
  | { kind: "error"; key: string; message: string }
  | { kind: "final"; key: string; status: string; message: string };

/** Folds the event list into display items: calls joined with their results, confirmations with decisions. */
export function toItems(events: CopilotEvent[]): Item[] {
  const items: Item[] = [];
  const tools = new Map<string, Extract<Item, { kind: "tool" }>>();
  const confirms = new Map<string, Extract<Item, { kind: "confirm" }>>();
  let turnStart = 0;
  const dedupeLinks = () => {
    // Show each link once per exchange, on the last step that offers it.
    const seen = new Set<string>();
    for (let i = items.length - 1; i >= turnStart; i--) {
      const it = items[i];
      if (it.kind !== "tool") continue;
      it.links = it.links.filter((l) => !seen.has(l.href) && seen.add(l.href));
    }
  };
  for (const e of events) {
    const key = `e${e.seq}`;
    switch (e.type) {
      case "user":
        dedupeLinks();
        turnStart = items.length;
        items.push({ kind: "user", key, text: e.payload.text });
        break;
      case "assistant":
        if (e.payload.text?.trim())
          items.push({ kind: "text", key, text: e.payload.text, progress: !("tool_calls" in e.payload) });
        break;
      case "tool_call": {
        const it = { kind: "tool" as const, key, id: e.payload.id, tool: e.payload.tool, args: e.payload.args || {}, links: [] };
        tools.set(e.payload.id, it);
        items.push(it);
        break;
      }
      case "tool_result": {
        const it = tools.get(e.payload.id);
        if (it) {
          it.ok = e.payload.ok;
          it.summary = e.payload.summary;
          it.links = e.payload.ok ? e.payload.links || [] : [];
        }
        break;
      }
      case "confirm_request": {
        const it = { kind: "confirm" as const, key, req: e.payload };
        confirms.set(e.payload.id, it);
        items.push(it);
        break;
      }
      case "confirm_resolved": {
        const it = confirms.get(e.payload.id);
        if (it) it.resolved = { approved: e.payload.approved, by: e.payload.by };
        break;
      }
      case "error":
        items.push({ kind: "error", key, message: e.payload.message });
        break;
      case "final":
        if (e.payload.status !== "done" && e.payload.message)
          items.push({ kind: "final", key, status: e.payload.status, message: e.payload.message });
        break;
    }
  }
  dedupeLinks();
  return items;
}

const ARG_KEYS = ["slug", "run_id", "case_id", "version", "name", "query", "topic", "improvement_id"];

/** "list_eval_cases(repo-maintainer)", "create_eval_case(repo-maintainer, refund-partial)". */
export function toolLabel(tool: string, args: Record<string, unknown>): string {
  const vals = ARG_KEYS.filter((k) => args[k] !== undefined && args[k] !== null && args[k] !== "")
    .slice(0, 2)
    .map((k) => {
      const v = String(args[k]);
      return v.length > 40 ? `${v.slice(0, 37)}...` : v;
    });
  return `${tool}(${vals.join(", ")})`;
}

function Links({ links, onNavigate }: { links: CopilotLink[]; onNavigate: (href: string) => void }) {
  if (!links.length) return null;
  return (
    <div className="cp-links">
      {links.map((l) => (
        <a
          key={l.href}
          href={l.href}
          className="cp-link"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey) return;
            e.preventDefault();
            onNavigate(l.href);
          }}
        >
          {l.label} →
        </a>
      ))}
    </div>
  );
}

export function ToolStep({ item, onNavigate }: { item: Extract<Item, { kind: "tool" }>; onNavigate: (href: string) => void }) {
  const tone = item.ok === undefined ? "pending" : item.ok ? "ok" : "error";
  return (
    <div className="cp-step" data-testid="tool-step">
      <div className="tx-row">
        <Dot tone={tone} />
        <div className="min-w-0 flex-1">
          <div className="break-words font-medium">{toolLabel(item.tool, item.args)}</div>
          <div className={`tx-result ${item.ok === false ? "text-red-700 dark:text-red-400" : "text-neutral-500 dark:text-neutral-400"}`}>
            <span className="tx-elbow" aria-hidden="true">
              ⎿
            </span>
            <div className="min-w-0 flex-1 break-words">{item.summary ?? <Spinner />}</div>
          </div>
          <Links links={item.links} onNavigate={onNavigate} />
        </div>
      </div>
    </div>
  );
}

export function ConfirmCard({
  item,
  onAnswer,
  busy,
}: {
  item: Extract<Item, { kind: "confirm" }>;
  onAnswer: (approve: boolean) => void;
  busy?: boolean;
}) {
  const { req, resolved } = item;
  const state = !resolved
    ? null
    : resolved.approved
      ? "Approved"
      : resolved.by === "timeout"
        ? "Expired without an answer"
        : resolved.by === "stopped"
          ? "Stopped"
          : "Denied";
  return (
    <div className="cp-confirm" role="group" aria-label="Confirm action" data-testid="confirm-card">
      <p className="font-medium text-neutral-900 dark:text-neutral-100">{req.summary}</p>
      {req.items.length > 1 && (
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-neutral-700 dark:text-neutral-300">
          {req.items.map((i) => (
            <li key={i.call_id}>{i.summary}</li>
          ))}
        </ul>
      )}
      <details className="mt-1.5 text-xs text-neutral-500">
        <summary className="cursor-pointer select-none">Details</summary>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px]">
          {req.items.map((i) => `${i.tool} ${JSON.stringify(i.args, null, 2)}`).join("\n\n")}
        </pre>
      </details>
      {state ? (
        <p className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${resolved?.approved ? "text-emerald-700 dark:text-emerald-400" : "text-neutral-500"}`}>
          {resolved?.approved ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />} {state}
        </p>
      ) : (
        <div className="mt-2.5 flex gap-2">
          <button type="button" className="btn-primary btn-sm" disabled={busy} onClick={() => onAnswer(true)}>
            Approve
          </button>
          <button type="button" className="btn-secondary btn-sm" disabled={busy} onClick={() => onAnswer(false)}>
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

export default function Conversation({
  items,
  onNavigate,
  onAnswer,
  answering,
}: {
  items: Item[];
  onNavigate: (href: string) => void;
  onAnswer: (confirmId: string, approve: boolean) => void;
  answering?: string | null;
}) {
  return (
    <div className="cp-conv" data-testid="assistant-conversation">
      {items.map((it) => {
        switch (it.kind) {
          case "user":
            return (
              <div key={it.key} className="cp-user" data-testid="user-bubble">
                {it.text}
              </div>
            );
          case "text":
            return it.progress ? (
              <p key={it.key} className="cp-note">
                {it.text}
              </p>
            ) : (
              <div key={it.key} className="cp-text" data-testid="assistant-text">
                <Markdown text={it.text} onNavigate={onNavigate} />
              </div>
            );
          case "tool":
            return <ToolStep key={it.key} item={it} onNavigate={onNavigate} />;
          case "confirm":
            return (
              <ConfirmCard
                key={it.key}
                item={it}
                busy={answering === it.req.id}
                onAnswer={(approve) => onAnswer(it.req.id, approve)}
              />
            );
          case "error":
            return (
              <div key={it.key} className="tx-error text-xs">
                {it.message}
              </div>
            );
          case "final":
            return (
              <p key={it.key} className="cp-note">
                {it.message}
              </p>
            );
        }
      })}
    </div>
  );
}
