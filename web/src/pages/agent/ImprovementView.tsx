import { Link } from "react-router-dom";
import { AlertTriangle, Check, ExternalLink, Minus, Pencil, Plus, X } from "lucide-react";
import type { Ablation, CaseResult, Improvement, InstructionConflict, LessonDiff, Score } from "../../api";
import StatusPill from "../../components/StatusPill";
import { Badge, ErrorBox, SplitBadge } from "../../components/ui";

export const PRUNE_RULE =
  "Pruning rule: a candidate is eligible when no case regresses and it passes at least as many cases as base. The goal is a smaller prompt at equal quality.";

const VERDICT: Record<Ablation["verdict"], { label: string; tone: "red" | "green" | "neutral" }> = {
  unhelpful: { label: "removed", tone: "red" },
  helpful: { label: "kept", tone: "green" },
  unknown: { label: "kept", tone: "neutral" },
  not_ablated: { label: "not tested", tone: "neutral" },
};

function AblationList({ ablations }: { ablations: Ablation[] }) {
  return (
    <div>
      <h4 className="mb-2 text-sm font-medium">Ablation on train cases</h4>
      <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
        {ablations.map((a) => (
          <li key={a.lesson} data-testid="ablation" data-verdict={a.verdict} className="flex flex-wrap items-start gap-2 px-3 py-2 text-sm">
            <Badge tone={VERDICT[a.verdict].tone}>{VERDICT[a.verdict].label}</Badge>
            <span className="min-w-0 flex-1">{a.lesson}</span>
            <span className="w-full text-xs text-neutral-500 sm:w-auto">
              {a.evidence}
              {a.version ? ` (v${a.version})` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ScoreText({ score }: { score: Score }) {
  return (
    <span className="font-semibold tabular-nums">
      {score.passed}
      <span className="font-normal text-neutral-400">/{score.total}</span>
    </span>
  );
}

/** "2/3" when the verdict is a majority over several runs; null for a single run or unknown counts. */
export function passFraction(passes?: number, runs?: number): string | null {
  return runs && runs > 1 && passes !== undefined ? `${passes}/${runs}` : null;
}

export function PassCell({
  passed,
  reason,
  runId,
  passes,
  runs,
}: {
  passed: boolean;
  reason?: string;
  runId?: string | null;
  passes?: number;
  runs?: number;
}) {
  const fraction = passFraction(passes, runs);
  const verdict = passed ? "passed" : "failed";
  const body = (
    <span
      title={(fraction ? `${verdict} ${fraction} runs (majority). ` : "") + (reason || verdict)}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${
        passed
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          : "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
      }`}
    >
      {passed ? <Check className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
      {passed ? "pass" : "fail"}
      {fraction && (
        <span data-testid="pass-fraction" className="font-mono tabular-nums opacity-80">
          {fraction}
        </span>
      )}
      {runId && <ExternalLink className="h-3 w-3 opacity-60" />}
    </span>
  );
  return runId ? <Link to={`/runs/${runId}`}>{body}</Link> : body;
}

function Cell({ r }: { r: CaseResult | null }) {
  if (!r) return <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />;
  return <PassCell passed={r.passed} reason={r.reason} runId={r.run_id} passes={r.passes} runs={r.runs} />;
}

function ScoreCard({ split, im }: { split: "train" | "holdout"; im: Improvement }) {
  const base = im.base?.[split];
  const cand = im.candidate?.[split];
  const holdout = split === "holdout";
  const better = base && cand && cand.passed > base.passed;
  const worse = base && cand && cand.passed < base.passed;
  return (
    <div
      data-testid={`improvement-score-${split}`}
      className={`rounded-xl border p-4 ${
        holdout
          ? "border-amber-300 bg-amber-50/50 ring-1 ring-amber-200 dark:border-amber-800 dark:bg-amber-950/20 dark:ring-amber-900"
          : "border-neutral-200 dark:border-neutral-800"
      }`}
    >
      <div className="mb-3 flex items-center gap-2">
        <SplitBadge split={split} />
        {holdout && <span className="text-xs text-amber-700 dark:text-amber-400">unseen by optimizer</span>}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-neutral-500">base v{im.base_version}</div>
          <div className="text-2xl" data-testid="base-score">{base ? <ScoreText score={base} /> : "..."}</div>
        </div>
        <div>
          <div className="text-xs text-neutral-500">candidate {im.candidate_version ? `v${im.candidate_version}` : ""}</div>
          <div data-testid="candidate-score" className={`text-2xl ${better ? "text-emerald-600 dark:text-emerald-400" : worse ? "text-red-600 dark:text-red-400" : ""}`}>
            {cand ? <ScoreText score={cand} /> : "..."}
          </div>
        </div>
      </div>
    </div>
  );
}

function LessonOp({ d }: { d: LessonDiff }) {
  const style = {
    add: { icon: <Plus className="h-3.5 w-3.5" />, cls: "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40", text: "text-emerald-800 dark:text-emerald-300" },
    edit: { icon: <Pencil className="h-3.5 w-3.5" />, cls: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40", text: "text-amber-900 dark:text-amber-200" },
    remove: { icon: <Minus className="h-3.5 w-3.5" />, cls: "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40", text: "text-red-800 line-through dark:text-red-300" },
  }[d.op];
  return (
    <li data-testid="lesson-op" data-op={d.op} className={`rounded-lg border p-3 ${style.cls}`}>
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 ${style.text}`}>{style.icon}</span>
        <div className="min-w-0 flex-1 space-y-1.5">
          {d.op === "edit" && d.old_text && (
            <p className="text-sm text-neutral-500 line-through">{d.old_text}</p>
          )}
          <p className={`text-sm ${style.text}`}>{d.text}</p>
          {d.rationale && <p className="text-xs text-neutral-600 dark:text-neutral-400">{d.rationale}</p>}
          {d.evidence_run_ids?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 text-xs">
              <span className="text-neutral-500">Evidence:</span>
              {d.evidence_run_ids.map((id) => (
                <Link key={id} to={`/runs/${id}`} className="font-mono text-accent-600 hover:underline dark:text-accent-400">
                  {id}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/** Configure with the instructions field focused and, when given, the quoted instruction selected. */
export function reviewInstructionsHref(slug: string, quote?: string): string {
  const q = new URLSearchParams({ focus: "instructions" });
  if (quote) q.set("select", quote);
  return `/agents/${slug}/configure?${q.toString()}`;
}

/** Instruction conflicts: failures or feedback that come from the author's own instructions. Lessons never
 * override instructions, so the optimizer reports these here instead of writing a lesson. */
export function InstructionConflicts({ conflicts, slug }: { conflicts: InstructionConflict[]; slug?: string }) {
  if (conflicts.length === 0) return null;
  const firstQuote = conflicts.find((c) => c.instruction_quote)?.instruction_quote;
  return (
    <div
      data-testid="instruction-conflicts"
      role="note"
      className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40"
    >
      <div className="flex flex-wrap items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-medium text-amber-900 dark:text-amber-200">
            Your instructions conflict with what evaluations and feedback show
          </h4>
          <p className="mt-0.5 text-xs text-amber-800 dark:text-amber-300">
            Lessons never override your instructions, so the optimizer did not write a lesson for these. Only you can change the
            instructions.
          </p>
        </div>
        {slug && (
          <Link to={reviewInstructionsHref(slug, firstQuote)} className="btn-secondary shrink-0" data-testid="review-instructions">
            Review instructions
          </Link>
        )}
      </div>
      <ul className="space-y-2">
        {conflicts.map((c, i) => (
          <li
            key={i}
            data-testid="instruction-conflict"
            className="space-y-1.5 rounded-md border border-amber-200 bg-white/70 p-3 text-sm dark:border-amber-900 dark:bg-neutral-950/40"
          >
            {c.instruction_quote ? (
              <blockquote data-testid="conflict-quote" className="border-l-2 border-amber-400 pl-2 font-mono text-xs text-neutral-800 dark:text-neutral-200">
                {c.instruction_quote}
              </blockquote>
            ) : (
              <p className="text-xs text-neutral-500">The conflicting instruction could not be quoted.</p>
            )}
            <p className="text-neutral-800 dark:text-neutral-200">{c.problem}</p>
            {c.suggested_change && <p className="text-xs text-neutral-600 dark:text-neutral-400">Suggestion: {c.suggested_change}</p>}
            {c.evidence_run_ids.length > 0 && (
              <div className="flex flex-wrap gap-1.5 text-xs">
                <span className="text-neutral-500">Evidence:</span>
                {c.evidence_run_ids.map((id) => (
                  <Link key={id} to={`/runs/${id}`} className="font-mono text-accent-600 hover:underline dark:text-accent-400">
                    {id}
                  </Link>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ImprovementView({
  im,
  prodVersion,
  onPromote,
  slug,
}: {
  im: Improvement;
  prodVersion: number;
  onPromote: (n: number) => void;
  slug?: string;
}) {
  const name = (id: string) => im.cases.find((c) => c.case_id === id)?.name ?? id;
  const sortedCases = [...im.cases].sort((a, b) => (a.split === b.split ? a.name.localeCompare(b.name) : a.split === "train" ? -1 : 1));
  const canPromote = im.status === "eligible" && im.candidate_version !== null && im.candidate_version !== prodVersion;
  const prune = im.kind === "prune";

  return (
    <div className="card space-y-6 p-5" data-testid="improvement" data-status={im.status} data-kind={im.kind ?? "improve"}>
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill status={im.status} />
        {prune && <Badge tone="amber">Lesson pruning</Badge>}
        <span className="font-medium">
          base v{im.base_version} vs candidate {im.candidate_version ? `v${im.candidate_version}` : "(drafting)"}
        </span>
        <span className="font-mono text-xs text-neutral-400">{im.id}</span>
        <span className="flex-1" />
        {canPromote && (
          <button className="btn-primary" onClick={() => onPromote(im.candidate_version!)}>
            Promote v{im.candidate_version}
          </button>
        )}
      </div>
      {prune && (
        <p data-testid="prune-rule" className="text-sm text-neutral-600 dark:text-neutral-400">
          {PRUNE_RULE}
        </p>
      )}
      {im.status === "running" && (
        <p className="text-sm text-blue-600 dark:text-blue-400">
          {prune
            ? "Trying production without each lesson on train cases, then evaluating the pruned candidate. This updates every 2 seconds."
            : "Optimizer and evaluations are running. This updates every 2 seconds."}
        </p>
      )}
      {im.status === "rejected" && (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          {prune
            ? "Rejected: the pruned candidate lost a case that base passed, or there was nothing to remove."
            : "Rejected: the candidate did not pass more cases than base without regressions."}
        </p>
      )}
      {im.status === "needs_author" && (
        <p data-testid="needs-author" className="text-sm text-amber-800 dark:text-amber-300">
          {im.message || "No lessons were added because the problem is in your instructions. Review the conflicts below."}
        </p>
      )}
      <ErrorBox error={im.error} />
      <InstructionConflicts conflicts={im.instruction_conflicts ?? []} slug={slug} />

      {(im.base || im.candidate) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <ScoreCard split="train" im={im} />
          <ScoreCard split="holdout" im={im} />
        </div>
      )}

      {sortedCases.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
          <table className="w-full min-w-[480px]">
            <thead className="border-b border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950">
              <tr>
                <th className="th">Case</th>
                <th className="th">Split</th>
                <th className="th">base v{im.base_version}</th>
                <th className="th">candidate {im.candidate_version ? `v${im.candidate_version}` : ""}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
              {sortedCases.map((c) => (
                <tr key={c.case_id} data-testid="improvement-case" data-name={c.name}>
                  <td className="td font-mono text-xs">{c.name}</td>
                  <td className="td">
                    <SplitBadge split={c.split} />
                  </td>
                  <td className="td">
                    <Cell r={c.base} />
                  </td>
                  <td className="td">
                    <Cell r={c.candidate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(im.newly_passing.length > 0 || im.regressions.length > 0 || (prune && im.candidate_version !== null && im.status !== "running")) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <h4 className="mb-2 text-sm font-medium text-emerald-700 dark:text-emerald-400">Newly passing ({im.newly_passing.length})</h4>
            {im.newly_passing.length === 0 ? (
              <p className="text-sm text-neutral-500">None</p>
            ) : (
              <ul className="space-y-1">
                {im.newly_passing.map((id) => (
                  <li key={id} className="flex items-center gap-1.5 font-mono text-xs">
                    <Check className="h-3.5 w-3.5 text-emerald-500" /> {name(id)}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h4 className="mb-2 text-sm font-medium text-red-700 dark:text-red-400">Regressions ({im.regressions.length})</h4>
            {im.regressions.length === 0 ? (
              <p className="text-sm text-neutral-500">None</p>
            ) : (
              <ul className="space-y-1">
                {im.regressions.map((id) => (
                  <li key={id} className="flex items-center gap-1.5 font-mono text-xs">
                    <X className="h-3.5 w-3.5 text-red-500" /> {name(id)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {prune && im.ablations && im.ablations.length > 0 && <AblationList ablations={im.ablations} />}

      {im.lessons_diff.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium">{prune ? `Lessons removed (${im.lessons_diff.length})` : "Lesson changes"}</h4>
          <ul className="space-y-2">
            {im.lessons_diff.map((d, i) => (
              <LessonOp key={i} d={d} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
