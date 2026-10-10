import { Sparkles } from "lucide-react";
import type { SuggestedPrompt } from "../api";

/** Clickable starter chips for an agent's suggested prompts. Renders nothing when there are none. */
export default function SuggestedPrompts({
  prompts,
  onPick,
  selected,
}: {
  prompts?: SuggestedPrompt[];
  onPick: (p: SuggestedPrompt) => void;
  /** The prompt text currently in the input, to mark the matching chip as selected. */
  selected?: string;
}) {
  if (!prompts || prompts.length === 0) return null;
  return (
    <div data-testid="suggested-prompts" className="space-y-1.5">
      <p className="text-xs font-medium text-neutral-500 dark:text-neutral-400">Try one of these</p>
      <div className="flex flex-wrap gap-2">
        {prompts.map((p, i) => {
          const active = selected !== undefined && selected === p.prompt;
          return (
            <button
              key={`${i}-${p.title}`}
              type="button"
              title={p.prompt}
              aria-pressed={active}
              onClick={() => onPick(p)}
              className={
                "inline-flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors " +
                (active
                  ? "border-accent-400 bg-accent-50 text-accent-700 dark:border-accent-600 dark:bg-accent-900/30 dark:text-accent-300"
                  : "border-neutral-200 bg-white text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50 " +
                    "dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200 dark:hover:bg-neutral-800")
              }
            >
              <Sparkles className="h-3 w-3 shrink-0" />
              <span className="truncate">{p.title}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
