import { createContext, useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg, type ForgeEvent } from "../../api";
import { clip, splitLines, str } from "./model";
// Render-time import only (TranscriptBody renders tool rows, which render this): the cycle is safe.
import { TranscriptBody } from "./Transcript";

/** How deep we are in delegated-agent transcripts; matches the server's delegation depth limit. */
export const MAX_AGENT_DEPTH = 3;
export const DepthContext = createContext(0);

const STATUS_TONE: Record<string, string> = {
  succeeded: "text-emerald-700 dark:text-emerald-400",
  failed: "text-red-700 dark:text-red-400",
  timeout: "text-amber-700 dark:text-amber-400",
  cancelled: "text-neutral-500",
};

/** First non-empty line of the child's answer, trimmed for the `⎿` line. */
export function summarize(output: string, n = 120): string {
  const line = splitLines(output.trim()).find((l) => l.trim()) ?? "";
  return clip(line.replace(/[*_`#>]/g, "").trim(), n);
}

function NestedRun({ runId }: { runId: string }) {
  const depth = useContext(DepthContext);
  const [events, setEvents] = useState<ForgeEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .getEvents(runId)
      .then((evs) => live && setEvents([...evs].sort((a, b) => a.seq - b.seq)))
      .catch((e) => live && setError(errMsg(e)));
    return () => {
      live = false;
    };
  }, [runId]);
  if (error) return <div className="text-red-700 dark:text-red-400">Could not load the delegated run: {error}</div>;
  if (!events) return <div className="text-neutral-500">Loading delegated run…</div>;
  return (
    <DepthContext.Provider value={depth + 1}>
      <div className="tx-nested tx-body" data-testid="nested-run" data-depth={depth + 1}>
        <TranscriptBody events={events} live={false} now={0} />
      </div>
    </DepthContext.Provider>
  );
}

/** `⎿` line for a delegated agent: its status, a short summary, a link, and an expandable nested transcript. */
export function AgentBody({
  result,
  output,
  isError,
  open,
  setOpen,
}: {
  result: Record<string, unknown>;
  output: string;
  isError: boolean;
  open: boolean;
  setOpen: (v: boolean) => void;
}) {
  const depth = useContext(DepthContext);
  const childId = str(result.child_run_id);
  const status = str(result.child_status) || (isError ? "failed" : "succeeded");
  const canNest = Boolean(childId) && depth < MAX_AGENT_DEPTH;
  return (
    <div className="tx-result text-neutral-500 dark:text-neutral-400">
      <span className="tx-elbow" aria-hidden="true">
        ⎿
      </span>
      <div className="min-w-0 flex-1">
        <div data-testid="agent-summary">
          <span className={`font-semibold ${STATUS_TONE[status] ?? ""}`}>{status}</span>
          {summarize(output) && <span className="text-neutral-700 dark:text-neutral-300">: {summarize(output)}</span>}
        </div>
        <div className="flex flex-wrap gap-x-3">
          {canNest && (
            <button type="button" className="tx-more" onClick={() => setOpen(!open)} data-testid="agent-expand">
              {open ? "(click to collapse)" : "(click to expand the delegated run)"}
            </button>
          )}
          {childId && (
            <Link to={`/runs/${childId}`} className="tx-link underline-offset-2 hover:underline" data-testid="child-run-link">
              {childId}
            </Link>
          )}
        </div>
        {open && canNest && <NestedRun runId={childId} />}
      </div>
    </div>
  );
}
