import type { ImprovementStatus, RunStatus } from "../api";

const STYLES: Record<string, string> = {
  queued: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  running: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  succeeded: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  timeout: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  eligible: "bg-accent-50 text-accent-700 dark:bg-accent-900/40 dark:text-accent-300",
  promoted: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  rejected: "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
};

const DOTS: Record<string, string> = {
  queued: "bg-neutral-400",
  running: "bg-blue-500 animate-pulse",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  timeout: "bg-amber-500",
  eligible: "bg-accent-500",
  promoted: "bg-emerald-500",
  rejected: "bg-neutral-400",
};

export default function StatusPill({ status }: { status: RunStatus | ImprovementStatus | string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${
        STYLES[status] ?? STYLES.queued
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${DOTS[status] ?? DOTS.queued}`} />
      {status}
    </span>
  );
}
