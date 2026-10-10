import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, FolderOpen, History, MessageSquarePlus, ThumbsDown, ThumbsUp, X } from "lucide-react";
import {
  api,
  ApiError,
  errMsg,
  fmtTime,
  isTerminal,
  setShareVisitor,
  type Agent,
  type ForgeEvent,
  type Run,
  type Session,
} from "../../api";
import { useRunEvents } from "../useRunEvents";
import FilesPanel from "../FilesPanel";
import GenerateTaskButton from "../GenerateTaskButton";
import Composer, { type ComposerHandle } from "../playground/Composer";
import Markdown from "../transcript/Markdown";
import { ExpandContext } from "../transcript/tools";
import { buildItems, plural } from "../transcript/model";
import { JumpPill, StatusLine, TranscriptBody, scrollKeyFor, useAutoScroll, useNow } from "../transcript/Transcript";
import "../transcript/transcript.css";
import "../playground/playground.css";
import "./share.css";

/** Plain messages for the share link limits (CONTRACT.md "Share and API conversations"). */
export const LIMIT_MESSAGES: Record<string, string> = {
  daily_turns: "You've reached today's limit for this link. Try again tomorrow.",
  session_turns: "This conversation reached its message limit. Start a new conversation to keep going.",
  sessions_per_visitor:
    "You have the most open conversations this link allows. Continue a recent one, or try again later.",
  concurrent_turns: "This link is busy with other conversations right now. Try again in a moment.",
};

export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return (e.limit && LIMIT_MESSAGES[e.limit]) || e.message;
    if (e.status === 402) return "This agent has reached its usage limit for this month. Try again later.";
    if (e.status === 410) return "This conversation expired and is now read only. Start a new conversation to keep going.";
    if (e.status === 404) return "This conversation is not available. Start a new conversation.";
  }
  return errMsg(e);
}

const visitorKey = (slug: string) => `forge.share.visitor.${slug}`;

function loadVisitor(slug: string): string | null {
  try {
    return window.localStorage.getItem(visitorKey(slug));
  } catch {
    return null;
  }
}

function saveVisitor(slug: string, visitor: string) {
  try {
    window.localStorage.setItem(visitorKey(slug), visitor);
  } catch {
    /* private mode: the visitor lasts for this page only */
  }
}

/** The final answer event's seq (the last `assistant` event of kind final), or -1. */
function finalSeq(events: ForgeEvent[]): number {
  const items = buildItems(events);
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === "narration" && it.final) return it.seq;
  }
  return -1;
}

function TurnThumbs({ run, share }: { run: Run; share: string }) {
  const last = run.feedback?.length ? run.feedback[run.feedback.length - 1].rating : null;
  const [rating, setRating] = useState<1 | -1 | null>(last === 1 || last === -1 ? last : null);
  const [error, setError] = useState<string | null>(null);
  async function rate(r: 1 | -1) {
    try {
      await api.feedback(run.id, r, "", share);
      setRating(r);
      setError(null);
    } catch (e) {
      setError(friendlyError(e));
    }
  }
  return (
    <div className="sc-thumbs" data-testid="turn-feedback">
      <button type="button" aria-label="Helpful" aria-pressed={rating === 1} onClick={() => rate(1)}>
        <ThumbsUp className="h-4 w-4" />
      </button>
      <button type="button" aria-label="Not helpful" aria-pressed={rating === -1} onClick={() => rate(-1)}>
        <ThumbsDown className="h-4 w-4" />
      </button>
      {rating !== null && <span className="text-xs text-neutral-500">Thanks for the feedback</span>}
      {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}

/** One exchange: the visitor's message, the agent's steps (collapsed) and its final answer. */
export function ShareTurn({
  run,
  events,
  live,
  now,
  share,
}: {
  run: Run;
  events: ForgeEvent[];
  live: boolean;
  now: number;
  share: string;
}) {
  const fin = useMemo(() => finalSeq(events), [events]);
  const steps = useMemo(() => events.filter((e) => e.seq !== fin), [events, fin]);
  const stepCount = useMemo(() => buildItems(steps).filter((i) => i.kind === "tool").length, [steps]);
  const answer = fin >= 0 ? String(events.find((e) => e.seq === fin)?.payload.text ?? "").trim() : "";
  const finished = !live && isTerminal(run.status);
  const fallback = finished && !answer && run.status === "succeeded" ? (run.output || "").trim() : "";
  return (
    <section className="sc-turn" data-testid="turn" data-turn={run.turn_index ?? undefined} data-status={run.status}>
      <div className="sc-user" data-testid="user-message">
        <p className="whitespace-pre-wrap break-words">{run.input}</p>
      </div>
      <div className="sc-agent">
        {(stepCount > 0 || live) && (
          <details className="sc-steps" data-testid="steps">
            <summary>
              {live ? (stepCount ? `Working · ${plural(stepCount, "step")} so far` : "Working") : `Show ${plural(stepCount, "step")}`}
            </summary>
            <div className="tx tx-body mt-3">
              <TranscriptBody events={steps} run={run} live={live} now={now} />
            </div>
          </details>
        )}
        {(answer || fallback) && (
          <div className="sc-answer" data-testid="final-answer">
            <Markdown text={answer || fallback} />
          </div>
        )}
        {finished && run.status === "cancelled" && <p className="sc-note">Stopped.</p>}
        {finished && run.status !== "succeeded" && run.status !== "cancelled" && (
          <p className="sc-note sc-note-error">The agent could not finish this request. Try asking again.</p>
        )}
        {finished && <TurnThumbs run={run} share={share} />}
      </div>
    </section>
  );
}

function StoredTurn({ run, now, share }: { run: Run; now: number; share: string }) {
  const { events } = useRunEvents(run.id, share);
  return <ShareTurn run={run} events={events} live={false} now={now} share={share} />;
}

/**
 * The share page conversation: multi turn chat with an agent's production version for one visitor. Reuses the
 * Playground composer, the transcript rows and FilesPanel; shows no configuration. `initialSession` and
 * `onSessionChange` let the page keep the open conversation in its URL.
 */
export default function ShareChat({
  agent,
  share,
  initialSession,
  onSessionChange,
}: {
  agent: Pick<Agent, "slug" | "name" | "description" | "suggested_prompts">;
  share: string;
  initialSession?: string | null;
  onSessionChange?: (id: string | null) => void;
}) {
  const slug = agent.slug;
  const [visitor, setVisitor] = useState<string | null>(() => {
    const v = loadVisitor(slug);
    setShareVisitor(share, v);
    return v;
  });
  const [activeId, setActiveId] = useState<string | null>(initialSession ?? null);
  const [session, setSession] = useState<Session | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  // The page passes a new callback each render; read it through a ref so loaders stay stable.
  const onChange = useRef(onSessionChange);
  onChange.current = onSessionChange;

  const open = useCallback(
    (id: string | null) => {
      setActiveId(id);
      setShowFiles(false);
      setShowHistory(false);
      setNotice(null);
      if (!id) setSession(null);
      onChange.current?.(id);
    },
    [],
  );

  const loadSessions = useCallback(async () => {
    if (!visitor) return;
    try {
      setSessions(await api.listSessions(slug, 8, share));
    } catch {
      /* the list is a convenience */
    }
  }, [slug, share, visitor]);

  const loadSession = useCallback(
    async (id: string) => {
      try {
        setSession(await api.getSession(id, share));
      } catch (e) {
        setSession(null);
        setNotice(friendlyError(e));
        if (e instanceof ApiError && e.status === 404) {
          setActiveId(null); // someone else's or a deleted conversation: start fresh
          onChange.current?.(null);
        }
      }
    },
    [share],
  );

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);
  useEffect(() => {
    if (activeId) loadSession(activeId);
  }, [activeId, loadSession]);

  const turns = session?.turns ?? [];
  const latest = turns.length ? turns[turns.length - 1] : null;
  const { events, done } = useRunEvents(latest?.id, share);
  const running = Boolean(latest && !isTerminal(latest.status) && !done);
  const now = useNow(running);
  const lastFinished = [...turns].reverse().find((t) => isTerminal(t.status) && (t !== latest || !running));
  const workspaceRun = lastFinished && lastFinished.has_artifacts !== false ? lastFinished : null;
  const expired = Boolean(session?.expired) && !running;
  const full = Boolean(session?.max_turns && turns.length >= session.max_turns) && !running;
  const scroller = useRef<HTMLDivElement>(null);
  const { endRef, showJump, jump } = useAutoScroll(
    `${turns.length}:${latest?.status}:${scrollKeyFor(events)}`,
    turns.length > 0,
    scroller,
  );

  // When the live turn finishes, refresh the session for its final status (the run row can lag the event).
  useEffect(() => {
    if (!done || !activeId) return;
    let cancelled = false;
    (async () => {
      for (let i = 0; i < 25 && !cancelled; i++) {
        try {
          const s = await api.getSession(activeId, share);
          if (cancelled) return;
          setSession(s);
          const last = s.turns?.[s.turns.length - 1];
          if (!last || isTerminal(last.status)) break;
        } catch {
          break;
        }
        await new Promise((r) => setTimeout(r, 300));
      }
      if (!cancelled) loadSessions();
    })();
    return () => {
      cancelled = true;
    };
  }, [done, activeId, share, loadSessions]);

  useEffect(() => {
    if (!running) composer.current?.focus();
  }, [running, activeId]);

  const stop = useCallback(async () => {
    if (!latest) return;
    try {
      await api.cancelRun(latest.id, share);
    } catch (e) {
      setNotice(friendlyError(e));
    }
    if (activeId) loadSession(activeId);
  }, [latest, activeId, share, loadSession]);

  async function send(input: string) {
    setBusy(true);
    setNotice(null);
    try {
      let id = activeId;
      if (!id) {
        const s = await api.createSession(slug, {}, share);
        if (s.visitor_id && s.visitor_id !== visitor) {
          saveVisitor(slug, s.visitor_id);
          setShareVisitor(share, s.visitor_id);
          setVisitor(s.visitor_id);
        }
        id = s.id;
        setSession(s);
        setActiveId(id);
        onChange.current?.(id);
      }
      await api.sendMessage(id, input, share);
      setText("");
      await loadSession(id);
      loadSessions();
    } catch (e) {
      setNotice(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  const suggestions = agent.suggested_prompts ?? [];
  const recent = sessions.filter((s) => (s.turn_count ?? 0) > 0);
  const locked = expired || full;

  return (
    <ExpandContext.Provider value={{ open: false, epoch: 0 }}>
      <div className="sc" data-testid="share-chat">
        <div className="sc-bar">
          <div className="min-w-0 truncate text-sm font-medium" data-testid="conversation-title">
            {session?.title || "New conversation"}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {workspaceRun && (
              <button
                type="button"
                className={`sc-btn ${showFiles ? "sc-btn-on" : ""}`}
                onClick={() => {
                  setShowFiles((v) => !v);
                  setShowHistory(false);
                }}
                aria-pressed={showFiles}
                aria-label="Files"
              >
                <FolderOpen className="h-4 w-4" /> <span className="hidden sm:inline">Files</span>
              </button>
            )}
            {recent.length > 0 && (
              <button
                type="button"
                className={`sc-btn ${showHistory ? "sc-btn-on" : ""}`}
                onClick={() => {
                  setShowHistory((v) => !v);
                  setShowFiles(false);
                }}
                aria-pressed={showHistory}
                aria-label="Recent conversations"
              >
                <History className="h-4 w-4" /> <span className="hidden sm:inline">Recent</span>
              </button>
            )}
            <button
              type="button"
              className="sc-btn"
              onClick={() => {
                open(null);
                composer.current?.focus();
              }}
              disabled={busy}
              aria-label="New conversation"
            >
              <MessageSquarePlus className="h-4 w-4" /> <span className="hidden sm:inline">New conversation</span>
            </button>
          </div>
        </div>

        {showHistory && (
          <div className="sc-panel" data-testid="recent-conversations">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-medium text-neutral-500">Your recent conversations</span>
              <button type="button" className="sc-icon" aria-label="Close" onClick={() => setShowHistory(false)}>
                <X className="h-4 w-4" />
              </button>
            </div>
            <ul className="space-y-0.5">
              {recent.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    className={`pg-session ${s.id === activeId ? "pg-session-active" : ""}`}
                    onClick={() => open(s.id)}
                    data-testid="conversation-item"
                  >
                    <span className="block truncate">{s.title || "Untitled conversation"}</span>
                    <span className="mt-0.5 block text-[11px] text-neutral-500">
                      {fmtTime(s.updated_at)}
                      {s.expired ? " · read only" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {showFiles && workspaceRun && (
          <div className="sc-panel sc-files" data-testid="share-files">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs text-neutral-500">Files the agent has worked on in this conversation</span>
              <a href={api.artifactsUrl(workspaceRun.id, share)} className="sc-btn" download data-testid="download-workspace">
                <Download className="h-4 w-4" /> Download
              </a>
            </div>
            <div className="h-[min(22rem,45dvh)]">
              <FilesPanel runId={workspaceRun.id} share={share} onClose={() => setShowFiles(false)} />
            </div>
          </div>
        )}

        <div className="sc-scroll" ref={scroller} data-testid="share-transcript">
          {turns.length === 0 ? (
            <div className="sc-empty">
              <p className="text-sm text-neutral-600 dark:text-neutral-400">
                Ask <span className="font-medium text-neutral-900 dark:text-neutral-100">{agent.name}</span> anything it can
                help with. You can keep the conversation going with follow up messages.
              </p>
              {suggestions.length > 0 && (
                <div className="pg-suggestions" data-testid="suggested-prompts">
                  {suggestions.map((s, i) => (
                    <button key={i} type="button" className="pg-suggestion" disabled={busy} onClick={() => send(s.prompt)}>
                      <span className="block text-sm font-medium">{s.title}</span>
                      <span className="mt-1 line-clamp-2 block text-xs text-neutral-500">{s.prompt}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="sc-turns">
              {turns.map((t, i) =>
                i === turns.length - 1 ? (
                  <ShareTurn key={t.id} run={t} events={events} live={running} now={now} share={share} />
                ) : (
                  <StoredTurn key={t.id} run={t} now={now} share={share} />
                ),
              )}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="sc-bottom">
          {showJump && <JumpPill onClick={jump} />}
          {running && latest && (
            <div className="tx text-[13px]">
              <StatusLine events={events} run={latest} live now={now} hint="esc to stop" />
            </div>
          )}
          {notice && (
            <div className="sc-notice" role="status" data-testid="share-notice">
              {notice}
            </div>
          )}
          {locked && !notice && (
            <div className="sc-notice" role="status" data-testid="share-notice">
              {expired
                ? "This conversation expired and is now read only. Start a new conversation to keep going."
                : LIMIT_MESSAGES.session_turns}
            </div>
          )}
          {!locked && (
            <Composer
              ref={composer}
              value={text}
              onChange={setText}
              onSend={send}
              onStop={stop}
              running={running}
              busy={busy}
              simple
              lastPrompt={latest?.input}
              placeholder={turns.length ? "Reply to the agent" : "What would you like the agent to do?"}
              actions={
                turns.length === 0
                  ? (inputRef) => (
                      <GenerateTaskButton slug={slug} share={share} value={text} inputRef={inputRef} onGenerated={setText} disabled={busy} />
                    )
                  : undefined
              }
            />
          )}
        </div>
      </div>
    </ExpandContext.Provider>
  );
}
