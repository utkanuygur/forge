import { useState } from "react";

export type RunViewMode = "transcript" | "timeline";
const KEY = "forge.runView";

/** Remembered per viewer; storage can be unavailable (private mode), so every access is guarded. */
export function useRunViewMode(): [RunViewMode, (m: RunViewMode) => void] {
  const [mode, setMode] = useState<RunViewMode>(() => {
    try {
      return localStorage.getItem(KEY) === "timeline" ? "timeline" : "transcript";
    } catch {
      return "transcript";
    }
  });
  const set = (m: RunViewMode) => {
    setMode(m);
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* ignore */
    }
  };
  return [mode, set];
}

export default function ViewToggle({ mode, onChange }: { mode: RunViewMode; onChange: (m: RunViewMode) => void }) {
  const opts: { value: RunViewMode; label: string }[] = [
    { value: "transcript", label: "Transcript" },
    { value: "timeline", label: "Timeline" },
  ];
  return (
    <div
      role="group"
      aria-label="Run view"
      className="inline-flex rounded-lg border border-neutral-200 p-0.5 text-xs dark:border-neutral-800"
    >
      {opts.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={mode === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 font-medium transition ${
            mode === o.value
              ? "bg-neutral-100 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100"
              : "text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
