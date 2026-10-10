import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { History, Plus, Sparkles, X } from "lucide-react";
import { api, errMsg } from "../../api";
import { copilot, type CopilotEvent, type CopilotSession } from "../../copilotApi";
import { publishRefresh } from "../../lib/refresh";
import Composer, { type ComposerHandle } from "../playground/Composer";
import Conversation, { toItems } from "./Conversation";
import { pageInfo } from "./pageContext";
import { MAX_WIDTH, MIN_WIDTH } from "./usePanel";
import "../transcript/transcript.css";
import "../playground/playground.css";
import "./copilot.css";

const SESSION_KEY = "forge.assistant.session";

function readSession(): string | null {
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function writeSession(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(SESSION_KEY, id);
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage blocked */
  }
}

const names = new Map<string, string>();

/** The agent's display name for the header (cached; falls back to the slug). */
function useAgentName(slug?: string): string | undefined {
  const [name, setName] = useState(slug ? names.get(slug) : undefined);
  useEffect(() => {
    if (!slug) return setName(undefined);
    if (names.has(slug)) return setName(names.get(slug));
    setName(slug);
    let alive = true;
    api
      .getAgent(slug)
      .then((a) => {
        names.set(slug, a.name);
        if (alive) setName(a.name);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [slug]);
  return name;
}

export function contextLabel(agentName: string | undefined, place: string): string {
  return agentName ? `${agentName} · ${place}` : place;
}

export default function CopilotPanel({
  open,
  onClose,
  width,
  onWidth,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  width: number;
  onWidth: (w: number) => void;
  wide: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const info = useMemo(() => pageInfo(location.pathname), [location.pathname]);
  const agentName = useAgentName(info.slug);
  const [sessionId, setSessionId] = useState<string | null>(readSession);
  const [events, setEvents] = useState<CopilotEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [answering, setAnswering] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<CopilotSession[]>([]);
  const abort = useRef<AbortController | null>(null);
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const lastPrompt = useRef("");

  const onEvent = useCallback((e: CopilotEvent) => {
    setEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e]));
    if (e.type === "tool_result" && e.payload.ok && e.payload.refresh?.length) publishRefresh(e.payload.refresh);
  }, []);

  const attach = useCallback(
    async (run: () => Promise<void>) => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setRunning(true);
      try {
        await run();
      } catch (e) {
        setError(errMsg(e));
      } finally {
        if (abort.current === ctl) {
          setRunning(false);
          abort.current = null;
        }
      }
    },
    [],
  );

  // Load the remembered session; re-attach when a turn is still running (after a reload).
  useEffect(() => {
    if (!sessionId) {
      setEvents([]);
      return;
    }
    let alive = true;
    copilot
      .getSession(sessionId)
      .then((s) => {
        if (!alive) return;
        setEvents(s.messages);
        if (s.running) {
          const after = s.messages.length ? s.messages[s.messages.length - 1].seq : -1;
          void attach(() => copilot.resume(s.id, after, onEvent, abort.current?.signal));
        }
      })
      .catch(() => {
        if (!alive) return;
        writeSession(null);
        setSessionId(null);
      });
    return () => {
      alive = false;
    };
  }, [sessionId, attach, onEvent]);

  useEffect(() => () => abort.current?.abort(), []);

  useEffect(() => {
    if (open) setTimeout(() => composer.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  async function send(text: string) {
    setError(null);
    lastPrompt.current = text;
    setInput("");
    let id = sessionId;
    try {
      if (!id) {
        id = (await copilot.createSession(info.context)).id;
        writeSession(id);
        setSessionId(id);
      }
    } catch (e) {
      setError(errMsg(e));
      return;
    }
    const sid = id;
    await attach(() => copilot.send(sid, text, info.context, onEvent, abort.current?.signal));
  }

  async function answer(confirmId: string, approve: boolean) {
    if (!sessionId) return;
    setAnswering(confirmId);
    try {
      await copilot.confirm(sessionId, confirmId, approve);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setAnswering(null);
    }
  }

  async function stop() {
    if (!sessionId) return;
    try {
      await copilot.cancel(sessionId);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  function newSession() {
    abort.current?.abort();
    writeSession(null);
    setSessionId(null);
    setEvents([]);
    setRunning(false);
    setShowHistory(false);
    setError(null);
    composer.current?.focus();
  }

  async function openHistory() {
    setShowHistory((v) => !v);
    try {
      setSessions(await copilot.listSessions(10));
    } catch (e) {
      setError(errMsg(e));
    }
  }

  function pick(id: string) {
    setShowHistory(false);
    if (id === sessionId) return;
    abort.current?.abort();
    setRunning(false);
    writeSession(id);
    setSessionId(id);
  }

  const go = useCallback(
    (href: string) => {
      navigate(href);
      if (!wide) onClose();
    },
    [navigate, wide, onClose],
  );

  // Drag the left edge to resize (wide screens only).
  function startResize(e: React.PointerEvent) {
    e.preventDefault();
    const move = (ev: PointerEvent) => onWidth(window.innerWidth - ev.clientX);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const items = useMemo(() => toItems(events), [events]);
  const label = contextLabel(agentName, info.place);

  return (
    <aside
      className={wide ? "cp-panel cp-panel-wide" : "cp-panel cp-panel-sheet"}
      style={wide ? { width } : undefined}
      hidden={!open}
      aria-label="Forge assistant"
      data-testid="assistant-panel"
    >
      {wide && (
        <div
          className="cp-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize assistant"
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={MAX_WIDTH}
          aria-valuenow={width}
          tabIndex={0}
          onPointerDown={startResize}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") onWidth(width + 16);
            if (e.key === "ArrowRight") onWidth(width - 16);
          }}
        />
      )}
      <div className="cp-header">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <Sparkles className="h-4 w-4 text-accent-600 dark:text-accent-400" /> Assistant
          </div>
          <div className="truncate text-xs text-neutral-500" data-testid="assistant-context">
            {label}
          </div>
        </div>
        <button type="button" className="cp-icon" onClick={openHistory} aria-label="Recent sessions" title="Recent sessions">
          <History className="h-4 w-4" />
        </button>
        <button type="button" className="cp-icon" onClick={newSession} aria-label="New session" title="New session">
          <Plus className="h-4 w-4" />
        </button>
        <button type="button" className="cp-icon" onClick={onClose} aria-label="Close assistant" title="Close (⌘J)">
          <X className="h-4 w-4" />
        </button>
      </div>
      {showHistory && (
        <div className="cp-history" data-testid="assistant-sessions">
          {sessions.length === 0 && <p className="px-3 py-2 text-xs text-neutral-500">No sessions yet.</p>}
          {sessions.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`cp-history-item ${s.id === sessionId ? "font-medium" : ""}`}
              onClick={() => pick(s.id)}
            >
              <span className="truncate">{s.title || "New session"}</span>
              <span className="shrink-0 text-[11px] text-neutral-400">
                {new Date(s.updated_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </span>
            </button>
          ))}
        </div>
      )}
      <div ref={scroller} className="cp-scroll tx">
        {items.length === 0 ? (
          <div className="cp-empty" data-testid="assistant-empty">
            <p className="font-sans text-sm text-neutral-700 dark:text-neutral-300">
              Ask how Forge works, or tell me what to do: add eval cases, explain a failed run, run an evaluation,
              set approval rules, budgets or schedules. I look things up right away and ask before changing anything
              or spending money.
            </p>
            <div className="cp-starters">
              {info.starters.map((s) => (
                <button key={s.label} type="button" className="cp-starter" disabled={running} onClick={() => send(s.text)}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <Conversation items={items} onNavigate={go} onAnswer={answer} answering={answering} />
        )}
        {running && items.length > 0 && !items.some((i) => i.kind === "confirm" && !i.resolved) && (
          <p className="cp-note mt-2" aria-live="polite">
            Working…
          </p>
        )}
        {error && <div className="tx-error mt-2 text-xs">{error}</div>}
      </div>
      {items.length > 0 && !running && (
        <div className="cp-starters cp-starters-inline">
          {info.starters.map((s) => (
            <button key={s.label} type="button" className="cp-starter" onClick={() => send(s.text)}>
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="cp-composer">
        <Composer
          ref={composer}
          value={input}
          onChange={setInput}
          onSend={send}
          onStop={stop}
          running={running}
          lastPrompt={lastPrompt.current}
          placeholder="Ask about Forge or say what to do"
        />
      </div>
    </aside>
  );
}
