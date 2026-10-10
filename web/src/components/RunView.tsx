import { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api, errMsg, fmtDuration, fmtTime, isTerminal, type Run } from "../api";
import { useRunEvents } from "./useRunEvents";
import TraceTimeline from "./TraceTimeline";
import Transcript from "./transcript/Transcript";
import ViewToggle, { useRunViewMode } from "./transcript/ViewToggle";
import DiffView from "./DiffView";
import FilesPanel from "./FilesPanel";
import FeedbackWidget from "./FeedbackWidget";
import StatusPill from "./StatusPill";
import SaveAsEval from "./SaveAsEval";
import { Badge, ErrorBox, Loading } from "./ui";
import StepTimeline from "./StepTimeline";
import { fmtUsd } from "../lib/format";

function Panel({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="card" aria-label={title}>
      <div className="flex items-center justify-between gap-2 border-b border-neutral-200 px-5 py-3 dark:border-neutral-800">
        <h2 className="text-sm font-semibold">{title}</h2>
        {right}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export default function RunView({
  runId,
  share,
  simple,
  header,
}: {
  runId: string;
  share?: string;
  simple?: boolean;
  header?: (run: Run) => React.ReactNode;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState(false);
  const { events, done, streamError } = useRunEvents(runId, share);
  const [savedMode, setMode] = useRunViewMode();
  // The share page (simple) always shows the transcript; the builder can switch to the timeline.
  const mode = simple ? "transcript" : savedMode;
  const toggle = simple ? undefined : <ViewToggle mode={mode} onChange={setMode} />;

  const load = useCallback(async () => {
    try {
      const r = await api.getRun(runId, share);
      setRun(r);
      setError(null);
      return r;
    } catch (e) {
      setError(errMsg(e));
      return null;
    }
  }, [runId, share]);

  useEffect(() => {
    setRun(null);
    setDiff(null);
    setShowFiles(false);
    load();
  }, [load]);

  // When the stream finishes, refresh the run to get final output and stats.
  useEffect(() => {
    if (done) load();
  }, [done, load]);

  // Approval and budget events change the run's status: refresh it when one arrives.
  const lastType = events.length ? events[events.length - 1].type : undefined;
  useEffect(() => {
    if (lastType === "approval_requested" || lastType === "approval_resolved" || lastType === "error") load();
  }, [events.length, lastType, load]);

  // Poll lightly while running in case the stream misses the end.
  useEffect(() => {
    if (!run || isTerminal(run.status)) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [run, load]);

  const terminal = run ? isTerminal(run.status) : false;
  useEffect(() => {
    if (!terminal) return;
    api
      .getDiff(runId, share)
      .then((d) => setDiff(typeof d === "string" ? d : ""))
      .catch(() => setDiff(""));
  }, [terminal, runId, share]);

  if (error && !run) return <ErrorBox error={error} />;
  if (!run) return <Loading label="Loading run" />;

  const live = !terminal && !done;

  return (
    <div className="space-y-6">
      {header?.(run)}
      {!simple && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-neutral-600 dark:text-neutral-400">
          <StatusPill status={run.status} />
          <span>
            Version <span className="font-medium text-neutral-900 dark:text-neutral-100">v{run.version_n}</span>
          </span>
          <Badge>{run.source}</Badge>
          {fmtDuration(run) && <span>{fmtDuration(run)}</span>}
          <span>
            {run.tokens_in.toLocaleString()} in / {run.tokens_out.toLocaleString()} out tokens
          </span>
          {(run.cache_read_tokens || run.cache_write_tokens) ? (
            <span data-testid="run-header-cache" title="Prompt-cache tokens (not included in cost yet)">
              cache {(run.cache_read_tokens ?? 0).toLocaleString()} read / {(run.cache_write_tokens ?? 0).toLocaleString()} write
            </span>
          ) : null}
          {run.child_usage && run.child_usage.tokens_in + run.child_usage.tokens_out > 0 && (
            <span data-testid="child-usage">
              plus {run.child_usage.tokens_in.toLocaleString()} in / {run.child_usage.tokens_out.toLocaleString()} out
              {run.child_usage.cost_usd > 0 ? ` (${fmtUsd(run.child_usage.cost_usd)})` : ""} in called agents
            </span>
          )}
          <span>{run.turns} turns</span>
          {run.cost_usd !== undefined && (
            <span data-testid="run-header-cost">
              {run.cost_usd === null ? "unknown price" : fmtUsd(run.cost_usd)}
              {run.cost_usd !== null && run.cost_note ? <span className="text-neutral-400"> ({run.cost_note})</span> : null}
            </span>
          )}
          <span className="text-neutral-400">{fmtTime(run.created_at)}</span>
        </div>
      )}
      {!simple && run.parent_run_id && (
        <p className="text-sm text-neutral-600 dark:text-neutral-400" data-testid="called-by">
          Called by{" "}
          <a href={`/runs/${run.parent_run_id}`} className="font-mono text-accent-700 hover:underline dark:text-accent-400">
            {run.parent_run_id}
          </a>
        </p>
      )}
      {simple && (
        <div>
          <StatusPill status={run.status} />
        </div>
      )}

      {mode === "transcript" && (
        <Panel title="Transcript" right={toggle}>
          <Transcript events={events} run={run} live={live} input={run.input} canApprove={!share} />
          {streamError && <p className="mt-3 text-xs text-amber-600">Live stream interrupted: {streamError}</p>}
        </Panel>
      )}

      {mode === "timeline" && (
      <Panel title="Input" right={toggle}>
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{run.input}</p>
      </Panel>
      )}

      {mode === "timeline" && (
      <Panel title="Trace">
        <TraceTimeline events={events} live={live} simple={simple} runId={run.id} canApprove={!share} />
        {streamError && <p className="mt-3 text-xs text-amber-600">Live stream interrupted: {streamError}</p>}
      </Panel>
      )}

      {!simple && (
        <Panel title="Step timeline">
          <StepTimeline runId={run.id} refreshKey={`${run.status}-${events.length}`} />
        </Panel>
      )}

      {mode === "timeline" && (terminal || run.output) && (
        <Panel title="Output">
          {run.output ? (
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{run.output}</p>
          ) : (
            <p className="text-sm text-neutral-500">No final output.</p>
          )}
          {run.error && (
            <div className="mt-3">
              <ErrorBox error={run.error} />
            </div>
          )}
        </Panel>
      )}

      {terminal && (diff || run.has_artifacts) && (
        <Panel
          title="Changes"
          right={
            run.has_artifacts && (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <div role="group" aria-label="Changes view" className="flex overflow-hidden rounded-lg border border-neutral-200 text-xs dark:border-neutral-700">
                  <button
                    type="button"
                    aria-pressed={!showFiles}
                    onClick={() => setShowFiles(false)}
                    className={`px-2.5 py-1.5 ${!showFiles ? "bg-neutral-100 font-medium dark:bg-neutral-800" : "text-neutral-500"}`}
                  >
                    Diff
                  </button>
                  <button
                    type="button"
                    aria-pressed={showFiles}
                    onClick={() => setShowFiles(true)}
                    className={`border-l border-neutral-200 px-2.5 py-1.5 dark:border-neutral-700 ${showFiles ? "bg-neutral-100 font-medium dark:bg-neutral-800" : "text-neutral-500"}`}
                  >
                    Files
                  </button>
                </div>
                <a href={api.artifactsUrl(run.id, share)} className="btn-secondary btn-sm" download>
                  <Download className="h-3.5 w-3.5" /> Workspace .tar.gz
                </a>
              </div>
            )
          }
        >
          {showFiles && run.has_artifacts ? (
            <FilesPanel runId={run.id} share={share} onClose={() => setShowFiles(false)} />
          ) : diff ? (
            <DiffView diff={diff} />
          ) : (
            <p className="text-sm text-neutral-500">No file changes.</p>
          )}
        </Panel>
      )}

      {terminal && (
        <div className={`grid gap-6 ${simple ? "" : "md:grid-cols-2"}`}>
          <Panel title="Feedback">
            <FeedbackWidget runId={run.id} share={share} existing={run.feedback} />
          </Panel>
          {!simple && (
            <Panel title="Save as test case">
              <SaveAsEval runId={run.id} />
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
