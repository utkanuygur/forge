import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { api, fmtUsd, type Governance } from "../api";

/** Polls an agent's approvals and spend summary (GET /agents/{slug}/governance). */
export function useGovernance(slug: string, refreshKey: unknown = null, intervalMs = 5000): Governance | null {
  const [gov, setGov] = useState<Governance | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .governance(slug)
        .then((g) => alive && setGov(g))
        .catch(() => undefined);
    load();
    const t = setInterval(load, intervalMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [slug, intervalMs, refreshKey]);
  return gov;
}

export function meterTone(state: Governance["state"]): string {
  if (state === "exceeded") return "bg-red-500";
  if (state === "warning") return "bg-amber-500";
  return "bg-emerald-500";
}

/** Month-to-date spend versus the monthly budget, as a small bar. Renders nothing without a budget. */
export function BudgetMeter({ gov }: { gov: Governance }) {
  if (gov.monthly_budget_usd === null || gov.monthly_budget_usd === undefined) return null;
  const pct = Math.min(100, Math.round((gov.fraction ?? 0) * 100));
  return (
    <span
      data-testid="budget-meter"
      data-state={gov.state}
      className="inline-flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400"
      title="Estimated spend this calendar month (UTC) against the monthly budget"
    >
      <span
        className="relative h-1.5 w-20 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"
        role="meter"
        aria-label="Monthly budget used"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <span className={`absolute inset-y-0 left-0 ${meterTone(gov.state)}`} style={{ width: `${pct}%` }} />
      </span>
      {fmtUsd(gov.month_to_date_usd)} of {fmtUsd(gov.monthly_budget_usd)} this month
    </span>
  );
}

export function PendingApprovalsBadge({ gov, slug }: { gov: Governance; slug: string }) {
  if (!gov.pending_approvals) return null;
  const n = gov.pending_approvals;
  const target = gov.awaiting_runs.length === 1 ? `/runs/${gov.awaiting_runs[0]}` : `/agents/${slug}/runs`;
  return (
    <Link
      to={target}
      data-testid="pending-approvals"
      className="inline-flex items-center gap-1 rounded-md bg-violet-50 px-1.5 py-0.5 text-xs font-medium text-violet-700 hover:underline dark:bg-violet-950 dark:text-violet-300"
    >
      <ShieldAlert className="h-3.5 w-3.5" />
      {n} {n === 1 ? "run awaits" : "runs await"} approval
    </Link>
  );
}

/** Banner shown on the agent page at 80% of the monthly budget and once it is used up. */
export function BudgetBanner({ gov }: { gov: Governance }) {
  if (gov.state !== "warning" && gov.state !== "exceeded") return null;
  const pct = Math.round((gov.fraction ?? 0) * 100);
  const text =
    gov.state === "exceeded"
      ? `Monthly budget used up (${fmtUsd(gov.month_to_date_usd)} of ${fmtUsd(gov.monthly_budget_usd)}). New runs and evaluations are refused until the budget is raised or the month ends.`
      : `This agent has used ${pct}% of its monthly budget (${fmtUsd(gov.month_to_date_usd)} of ${fmtUsd(gov.monthly_budget_usd)}).`;
  return (
    <div
      data-testid="budget-banner"
      data-state={gov.state}
      className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${
        gov.state === "exceeded"
          ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300"
          : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-200"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{text}</span>
    </div>
  );
}
