import { useState } from "react";
import { Check, ShieldAlert, ShieldCheck, ShieldX, X } from "lucide-react";
import { api, errMsg, type ForgeEvent } from "../api";

function bashCommand(args: Record<string, unknown>): string {
  return typeof args.cmd === "string" ? args.cmd : typeof args.command === "string" ? args.command : "";
}

export type ApprovalState = "pending" | "approved" | "denied" | "expired";

/** State of an approval from its request and (optional) resolution events. */
export function approvalState(resolved: ForgeEvent | undefined, ended: boolean): ApprovalState {
  if (!resolved) return ended ? "expired" : "pending";
  if (resolved.payload.decision === "approve") return "approved";
  return resolved.payload.by === "timeout" ? "expired" : "denied";
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export default function ApprovalCard({
  request,
  resolved,
  runId,
  canApprove,
  ended,
}: {
  request: ForgeEvent;
  resolved?: ForgeEvent;
  runId: string;
  canApprove?: boolean;
  ended?: boolean;
}) {
  const p = request.payload;
  const id = String(p.id);
  const name = String(p.name || "");
  const args = (p.args as Record<string, unknown>) || {};
  const rule = (p.rule as { description?: string; mode?: string }) || {};
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [sent, setSent] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const state = approvalState(resolved, Boolean(ended));

  async function decide(decision: "approve" | "deny") {
    setBusy(decision);
    setError(null);
    try {
      await api.resolveApproval(runId, id, decision, note.trim());
      setSent(decision);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  const tone =
    state === "pending"
      ? "border-violet-300 bg-violet-50/60 dark:border-violet-800 dark:bg-violet-950/30"
      : state === "approved"
        ? "border-emerald-200 bg-emerald-50/50 dark:border-emerald-900 dark:bg-emerald-950/20"
        : "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20";
  const Icon = state === "pending" ? ShieldAlert : state === "approved" ? ShieldCheck : ShieldX;
  const preview = name === "bash" ? bashCommand(args) : typeof args.path === "string" ? args.path : "";
  const rNote = resolved ? String(resolved.payload.note || "") : "";
  const by = resolved ? String(resolved.payload.by || "") : "";

  return (
    <div data-testid="approval-card" data-state={state} data-approval={id} className={`rounded-lg border p-3 text-sm ${tone}`}>
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="font-medium">
            {state === "pending" && "Approval needed before this call runs"}
            {state === "approved" && `Approved${by ? ` (${by})` : ""}`}
            {state === "denied" && `Denied${by ? ` (${by})` : ""}${rNote ? `: ${rNote}` : ""}`}
            {state === "expired" && (resolved ? `Denied automatically: ${rNote}` : "Expired without a decision")}
          </div>
          <div className="grid gap-1 text-xs text-neutral-600 dark:text-neutral-400">
            <div>
              Tool <span className="font-mono text-neutral-900 dark:text-neutral-100">{name}</span>
              {preview && (
                <>
                  {" "}
                  <span className="break-all font-mono text-neutral-900 dark:text-neutral-100">{preview}</span>
                </>
              )}
            </div>
            <div>
              Matched rule <span className="font-mono">{rule.description || "policy rule"}</span>
            </div>
            {state === "pending" && typeof p.expires_at === "number" && (
              <div>Denied automatically at {when(p.expires_at)} if nobody decides.</div>
            )}
          </div>
          {name !== "bash" && Object.keys(args).length > 0 && (
            <details>
              <summary className="cursor-pointer text-xs text-neutral-500">Arguments</summary>
              <pre className="mono-block mt-1 max-h-60">{JSON.stringify(args, null, 2)}</pre>
            </details>
          )}
          {state === "pending" && canApprove && !sent && (
            <div className="space-y-2">
              <input
                aria-label="Reviewer note"
                className="input"
                placeholder="Note for the agent (optional, sent with a denial)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary btn-sm" disabled={busy !== null} onClick={() => decide("approve")}>
                  <Check className="h-3.5 w-3.5" /> {busy === "approve" ? "Approving..." : "Approve"}
                </button>
                <button className="btn-secondary btn-sm" disabled={busy !== null} onClick={() => decide("deny")}>
                  <X className="h-3.5 w-3.5" /> {busy === "deny" ? "Denying..." : "Deny"}
                </button>
              </div>
            </div>
          )}
          {state === "pending" && sent && (
            <p className="text-xs text-neutral-500">{sent === "approve" ? "Approved" : "Denied"}. Waiting for the run to continue...</p>
          )}
          {state === "pending" && !canApprove && (
            <p className="text-xs text-neutral-500">Waiting for the agent owner to approve or deny this call.</p>
          )}
          {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
        </div>
      </div>
    </div>
  );
}
