import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { FlaskConical, MessageSquarePlus, Save, ThumbsDown, ThumbsUp } from "lucide-react";
import { api, errMsg, fmtTime, isTerminal, type Agent, type DraftConfig, type Run, type Session } from "../../api";
import { useRunEvents } from "../useRunEvents";
import { useToast } from "../Toast";
import { ErrorBox } from "../ui";
import { ExpandContext } from "../transcript/tools";
import {
  ExpandAllButton,
  JumpPill,
  StatusLine,
  TranscriptBody,
  scrollKeyFor,
  useAutoScroll,
  useExpandAll,
  useNow,
} from "../transcript/Transcript";
import Composer, { type ComposerHandle } from "./Composer";
import "../transcript/transcript.css";
import "./playground.css";

/** What a new session starts from: production, a specific version, or unsaved edits from Configure. */
export type StartFrom = { kind: "prod" } | { kind: "version"; n: number } | { kind: "draft"; draft: DraftConfig };

function startLabel(s: StartFrom, agent: Agent): string {
  if (s.kind === "prod") return `Production v${agent.prod_version}`;
  if (s.kind === "version") return `v${s.n}`;
  return "Unsaved draft";
}

function sessionLabel(s: Session): string {
  return s.version_n === null ? "draft" : `v${s.version_n}`;
}

/** Thumbs on a finished turn; feeds the improvement system like feedback on any run. */
function TurnFeedback({ run }: { run: Run }) {
  const [rating, setRating] = useState<1 | -1 | null>(run.feedback?.length ? run.feedback[run.feedback.length - 1].rating : null);
  const [error, setError] = useState<string | null>(null);
  async function rate(r: 1 | -1) {
    try {
      await api.feedback(run.id, r, "");
      setRating(r);
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }
  return (
    <div className="pg-feedback" data-testid="turn-feedback">
      <button type="button" aria-label="Good" aria-pressed={rating === 1} onClick={() => rate(1)}>
        <ThumbsUp className="h-3.5 w-3.5" />
      </button>
      <button type="button" aria-label="Needs work" aria-pressed={rating === -1} onClick={() => rate(-1)}>
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
      <a href={`/runs/${run.id}`} className="pg-runlink">
        {run.id}
      </a>
      {error && <span className="text-red-600">{error}</span>}
    </div>
  );
}

/** An earlier, finished turn: fetches its own events. */
function StoredTurn({ run, now }: { run: Run; now: number }) {
  const { events } = useRunEvents(run.id);
  return <Turn run={run} events={events} live={false} now={now} />;
}

function Turn({ run, events, live, now }: { run: Run; events: ReturnType<typeof useRunEvents>["events"]; live: boolean; now: number }) {
  return (
    <section className="pg-turn" data-testid="turn" data-turn={run.turn_index ?? undefined} data-status={run.status}>
      <TranscriptBody events={events} run={run} live={live} input={run.input} now={now} />
      {!live && isTerminal(run.status) && <TurnFeedback run={run} />}
    </section>
  );
}

export default function Playground({ agent, filesPanel }: { agent: Agent; filesPanel?: ReactNode }) {
  const toast = useToast();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const activeId = params.get("session");
  const versions = useMemo(() => [...(agent.versions || [])].sort((a, b) => b.n - a.n), [agent.versions]);

  // A draft handed over by Configure's "Test" button (router state), remembered for this page visit.
  const [draft, setDraft] = useState<DraftConfig | null>(
    () => (location.state as { draft?: DraftConfig } | null)?.draft ?? null,
  );
  const [startFrom, setStartFrom] = useState<StartFrom>(() => (draft ? { kind: "draft", draft } : { kind: "prod" }));
  useEffect(() => {
    const d = (location.state as { draft?: DraftConfig } | null)?.draft;
    if (d) {
      setDraft(d);
      setStartFrom({ kind: "draft", draft: d });
      const next = new URLSearchParams(params);
      next.delete("session");
      setParams(next, { replace: true, state: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions(agent.slug));
    } catch (e) {
      setError(errMsg(e));
    }
  }, [agent.slug]);

  const loadSession = useCallback(async (id: string) => {
    try {
      setSession(await api.getSession(id));
    } catch (e) {
      setError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);
  useEffect(() => {
    if (activeId) loadSession(activeId);
    else setSession(null);
  }, [activeId, loadSession]);

  const turns = session?.turns ?? [];
  const latest = turns.length ? turns[turns.length - 1] : null;
  const { events, done } = useRunEvents(latest?.id);
  const running = Boolean(latest && !isTerminal(latest.status) && !done);
  const now = useNow(running);
  const { expand, toggleAll } = useExpandAll();
  // Follow new content (including a turn's final answer) while the reader is at the bottom.
  const { endRef, showJump, jump } = useAutoScroll(
    `${turns.length}:${latest?.status}:${scrollKeyFor(events)}`,
    turns.length > 0,
    scroller,
  );

  // When the live turn finishes, refresh the session for final statuses and the sidebar order. The
  // run_finished event can land just before the server finalizes the run row, so poll briefly.
  useEffect(() => {
    if (!done || !activeId) return;
    let cancelled = false;
    (async () => {
      for (let i = 0; i < 25 && !cancelled; i++) {
        try {
          const s = await api.getSession(activeId);
          if (cancelled) return;
          setSession(s);
          const last = s.turns?.[s.turns.length - 1];
          if (!last || isTerminal(last.status)) break;
        } catch (e) {
          setError(errMsg(e));
          break;
        }
        await new Promise((r) => setTimeout(r, 300));
      }
      if (!cancelled) loadSessions();
    })();
    return () => {
      cancelled = true;
    };
  }, [done, activeId, loadSessions]);

  useEffect(() => {
    if (!running) composer.current?.focus();
  }, [running, activeId]);

  const stop = useCallback(async () => {
    if (!latest) return;
    try {
      await api.cancelRun(latest.id);
    } catch (e) {
      setError(errMsg(e));
    }
    if (activeId) loadSession(activeId);
  }, [latest, activeId, loadSession]);

  // Esc stops the running turn wherever focus is (the composer is disabled while running).
  useEffect(() => {
    if (!running) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        stop();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, stop]);

  function openSession(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("session", id);
    else next.delete("session");
    setParams(next);
  }

  async function send(input: string) {
    setBusy(true);
    setError(null);
    try {
      let id = activeId;
      if (!id) {
        const body =
          startFrom.kind === "draft"
            ? { draft: startFrom.draft }
            : startFrom.kind === "version"
              ? { version: startFrom.n }
              : {};
        const s = await api.createSession(agent.slug, body);
        id = s.id;
        setSession(s);
        openSession(id);
      }
      await api.sendMessage(id, input);
      setText("");
      await loadSession(id);
      loadSessions();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (!session) return;
    try {
      const v = await api.saveSessionVersion(session.id, "Saved from the playground");
      toast(`Saved v${v.n}. Promote it on Configure to put it in production.`);
    } catch (e) {
      setError(errMsg(e));
    }
  }

  const lastPrompt = turns.length ? turns[turns.length - 1].input : undefined;
  const suggestions = agent.suggested_prompts ?? [];
  const isDraft = session ? session.version_n === null : startFrom.kind === "draft";
  const options: { key: string; label: string; value: StartFrom }[] = [
    { key: "prod", label: `Production v${agent.prod_version}`, value: { kind: "prod" } },
    ...versions.map((v) => ({ key: `v${v.n}`, label: `v${v.n}`, value: { kind: "version", n: v.n } as StartFrom })),
    ...(draft ? [{ key: "draft", label: "Unsaved draft (from Configure)", value: { kind: "draft", draft } as StartFrom }] : []),
  ];
  const startKey = startFrom.kind === "prod" ? "prod" : startFrom.kind === "version" ? `v${startFrom.n}` : "draft";

  return (
    <ExpandContext.Provider value={expand}>
      <div className={`pg ${filesPanel ? "pg-with-files" : ""}`} data-testid="playground">
        <aside className="pg-sidebar" aria-label="Sessions">
          <div className="space-y-2 border-b border-neutral-200 p-3 dark:border-neutral-800">
            <label className="label mb-1 text-xs" htmlFor="pg-start">
              Start from
            </label>
            <select
              id="pg-start"
              className="input py-1.5 text-xs"
              value={startKey}
              onChange={(e) => {
                const o = options.find((x) => x.key === e.target.value);
                if (o) setStartFrom(o.value);
              }}
            >
              {options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary btn-sm w-full"
              onClick={() => {
                openSession(null);
                composer.current?.focus();
              }}
            >
              <MessageSquarePlus className="h-3.5 w-3.5" /> New session
            </button>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto p-1.5" data-testid="session-list">
            {sessions.length === 0 && <li className="px-2 py-3 text-xs text-neutral-500">No sessions yet.</li>}
            {sessions.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={`pg-session ${s.id === activeId ? "pg-session-active" : ""}`}
                  onClick={() => openSession(s.id)}
                  data-testid="session-item"
                  aria-current={s.id === activeId ? "true" : undefined}
                >
                  <span className="block truncate">{s.title || "Untitled session"}</span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-neutral-500">
                    <span className={s.version_n === null ? "pg-tag-draft" : "pg-tag"}>{sessionLabel(s)}</span>
                    {fmtTime(s.updated_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <div className="pg-main">
          {/* Phones: the sidebar is hidden, so sessions are picked here. */}
          <div className="flex items-center gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800 md:hidden">
            <select
              aria-label="Session"
              className="input min-w-0 flex-1 py-1.5 text-xs"
              value={activeId ?? ""}
              onChange={(e) => openSession(e.target.value || null)}
            >
              <option value="">New session · {startLabel(startFrom, agent)}</option>
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {(s.title || "Untitled session").slice(0, 40)} ({sessionLabel(s)})
                </option>
              ))}
            </select>
          </div>
          <div className="pg-topbar">
            <div className="min-w-0 truncate text-sm" data-testid="session-heading">
              {session ? (
                <>
                  <span className="font-medium">{session.title || "Untitled session"}</span>{" "}
                  <span className={isDraft ? "pg-tag-draft" : "pg-tag"}>{sessionLabel(session)}</span>
                </>
              ) : (
                <span className="text-neutral-500">
                  New session · <span className="font-medium text-neutral-700 dark:text-neutral-300">{startLabel(startFrom, agent)}</span>
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {session && isDraft && (
                <button type="button" className="btn-secondary btn-sm" onClick={saveDraft}>
                  <Save className="h-3.5 w-3.5" /> Save as new version
                </button>
              )}
              <ExpandAllButton open={expand.open} onClick={toggleAll} />
            </div>
          </div>
          {isDraft && (
            <div className="pg-draft-banner" data-testid="draft-banner">
              <FlaskConical className="h-3.5 w-3.5" /> Testing unsaved edits. Draft runs do not count toward metrics or
              evaluations.
            </div>
          )}

          <div className="pg-scroll" ref={scroller} data-testid="playground-transcript">
            <div className="tx tx-body pg-turns">
              {turns.length === 0 && (
                <div className="pg-empty">
                  <p className="font-sans text-sm text-neutral-500">
                    Talk to <span className="font-medium text-neutral-800 dark:text-neutral-200">{agent.name}</span>. Each
                    message runs as a turn in a fresh sandbox that keeps the files from the previous turn.
                  </p>
                  {suggestions.length > 0 && (
                    <div className="pg-suggestions" data-testid="suggested-prompts">
                      {suggestions.map((s, i) => (
                        <button key={i} type="button" className="pg-suggestion" disabled={busy} onClick={() => send(s.prompt)}>
                          <span className="block font-sans text-sm font-medium">{s.title}</span>
                          <span className="mt-1 line-clamp-2 block text-xs text-neutral-500">{s.prompt}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {turns.map((t, i) =>
                i === turns.length - 1 ? (
                  <Turn key={t.id} run={t} events={events} live={running} now={now} />
                ) : (
                  <StoredTurn key={t.id} run={t} now={now} />
                ),
              )}
              <div ref={endRef} />
            </div>
          </div>

          <div className="pg-bottom">
            {showJump && <JumpPill onClick={jump} />}
            {latest && (
              <div className="tx text-[13px]">
                <StatusLine events={events} run={latest} live={running} now={now} hint="esc to stop · ctrl+o to expand" />
              </div>
            )}
            <ErrorBox error={error} onDismiss={() => setError(null)} />
            <Composer
              ref={composer}
              value={text}
              onChange={setText}
              onSend={send}
              onStop={stop}
              running={running}
              busy={busy}
              lastPrompt={lastPrompt}
            />
          </div>
        </div>

        {/* Files panel slot: a right-side workspace browser is mounted here by feat/playground-extras. */}
        {filesPanel && <aside className="pg-files" aria-label="Files">{filesPanel}</aside>}
      </div>
    </ExpandContext.Provider>
  );
}

export function useOpenPlayground(slug: string) {
  const navigate = useNavigate();
  return (draft?: DraftConfig) => navigate(`/agents/${slug}/playground`, { state: draft ? { draft } : null });
}
