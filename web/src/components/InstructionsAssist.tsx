import { useEffect, useRef, useState, type RefObject } from "react";
import { Check, Loader2, Sparkles, X } from "lucide-react";
import type { InstructionsSuggestion } from "../api";
import { diffStats, lineDiff } from "../lib/textDiff";
import { useAssist } from "./AssistButton";
import DiffView from "./DiffView";

/**
 * "Improve" for the instructions box (assist kind "instructions"): the suggestion is shown as a diff against the
 * current text with Accept and Reject. Accept replaces the text (Undo restores it); nothing is saved until the
 * user saves a version.
 */
export default function InstructionsAssist({
  slug,
  value,
  onChange,
  inputRef,
}: {
  slug: string;
  value: string;
  onChange: (text: string) => void;
  /** The instructions textarea: Alt+G (Option+G) inside it generates. */
  inputRef?: RefObject<HTMLTextAreaElement>;
}) {
  const { generate, busy, error, clearError } = useAssist<InstructionsSuggestion>("instructions");
  const [goal, setGoal] = useState("");
  const [proposal, setProposal] = useState<{ base: string; result: InstructionsSuggestion } | null>(null);
  const [undo, setUndo] = useState<{ previous: string; accepted: string } | null>(null);

  async function run() {
    const res = await generate({
      slug,
      input: { current: value, ...(goal.trim() ? { goal: goal.trim() } : {}) },
      resultText: (r) => r.instructions,
    });
    if (res) setProposal({ base: value, result: res.result });
  }

  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    const el = inputRef?.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && (e.code === "KeyG" || e.key.toLowerCase() === "g")) {
        e.preventDefault();
        void runRef.current();
      }
    };
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, [inputRef]);

  const diff = proposal ? lineDiff(proposal.base, proposal.result.instructions) : "";
  const stats = proposal ? diffStats(diff) : null;
  const showUndo = undo && undo.accepted === value;

  return (
    <div className="space-y-2" data-testid="instructions-assist">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input h-8 min-w-[180px] flex-1 py-1 text-xs"
          aria-label="Goal for the suggestion (optional)"
          placeholder="Optional goal, e.g. be stricter about running the tests"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void run();
            }
          }}
        />
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => void run()}
          disabled={busy}
          aria-label="Generate an improved prompt"
          data-testid="assist-instructions"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Sparkles className="h-3.5 w-3.5" aria-hidden />}
          {busy ? "Generating…" : value.trim() ? "Improve" : "Generate"}
        </button>
        {showUndo && (
          <button
            type="button"
            className="text-xs font-medium text-accent-700 underline underline-offset-2 dark:text-accent-400"
            onClick={() => {
              onChange(undo.previous);
              setUndo(null);
            }}
          >
            Undo
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}{" "}
          <button type="button" className="underline underline-offset-2" onClick={clearError}>
            Dismiss
          </button>
        </p>
      )}
      {proposal && (
        <div className="space-y-2 rounded-lg border border-accent-200 bg-accent-50/40 p-3 dark:border-accent-900 dark:bg-accent-900/10" data-testid="instructions-proposal">
          <p className="text-sm">
            <span className="font-medium">Suggested change.</span> {proposal.result.summary}
            {stats && (
              <span className="ml-1 text-xs text-neutral-500">
                ({stats.added} added, {stats.removed} removed)
              </span>
            )}
          </p>
          <div className="max-h-80 overflow-auto">
            <DiffView diff={diff} />
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-secondary btn-sm" onClick={() => setProposal(null)}>
              <X className="h-3.5 w-3.5" /> Reject
            </button>
            <button
              type="button"
              className="btn-primary btn-sm"
              onClick={() => {
                setUndo({ previous: value, accepted: proposal.result.instructions });
                onChange(proposal.result.instructions);
                setProposal(null);
              }}
            >
              <Check className="h-3.5 w-3.5" /> Accept
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
