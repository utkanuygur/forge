import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api, errMsg, type SampleTask } from "../api";

const UNDO_MS = 10_000;
const AVOID_LIMIT = 10;

/**
 * Calls POST /v1/agents/{slug}/sample-task. Prompts generated earlier in this session are sent as `avoid`, so
 * repeated clicks give different tasks even before any run exists.
 */
export function useGenerateTask(slug: string, share?: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<string[]>([]);

  const generate = useCallback(
    async (opts: { version?: number; hint?: string } = {}): Promise<SampleTask | null> => {
      setBusy(true);
      setError(null);
      try {
        const task = await api.sampleTask(
          slug,
          { avoid: seen.current.slice(-AVOID_LIMIT), ...(share ? {} : opts) },
          share,
        );
        seen.current.push(task.prompt);
        return task;
      } catch (e) {
        setError(errMsg(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [slug, share],
  );

  return { generate, busy, error, clearError: () => setError(null) };
}

type TextInput = HTMLTextAreaElement | HTMLInputElement;

/**
 * "Generate" fills a task box with a realistic sample task for the agent.
 *
 * Required: `slug` and `onGenerated(text)` (set the box's value). Optional:
 * - `share`: the share token (share page); the server then uses prod and rate limits the link.
 * - `value`: the box's current text. When it is not empty, a generation replaces it and an "Undo" link
 *   restores it (calls `onGenerated(previous)`) for 10 seconds or until the text is edited.
 * - `inputRef`: the box. It is focused after a fill (caret at the end, nothing selected), and Alt+G
 *   (Option+G) inside it triggers a generation.
 * - `version`: builder only, the version whose prompt and files to use (default prod).
 */
export default function GenerateTaskButton({
  slug,
  onGenerated,
  disabled,
  share,
  value,
  inputRef,
  version,
  className = "",
}: {
  slug: string;
  onGenerated: (text: string) => void;
  disabled?: boolean;
  share?: string;
  value?: string;
  inputRef?: RefObject<TextInput>;
  version?: number;
  className?: string;
}) {
  const { generate, busy, error, clearError } = useGenerateTask(slug, share);
  const [undo, setUndo] = useState<{ previous: string; generated: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearUndo = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setUndo(null);
  }, []);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  // Editing the generated text makes Undo meaningless.
  useEffect(() => {
    if (undo && value !== undefined && value !== undo.generated) clearUndo();
  }, [value, undo, clearUndo]);

  const focusEnd = useCallback(() => {
    const el = inputRef?.current;
    if (!el) return;
    // After React has rendered the new value.
    setTimeout(() => {
      el.focus();
      const end = el.value.length;
      el.setSelectionRange(end, end);
    }, 0);
  }, [inputRef]);

  const run = useCallback(async () => {
    if (busy || disabled) return;
    const previous = value ?? "";
    const task = await generate({ version });
    if (!task) return;
    clearUndo();
    onGenerated(task.prompt);
    if (previous.trim() && previous !== task.prompt) {
      setUndo({ previous, generated: task.prompt });
      timer.current = setTimeout(() => setUndo(null), UNDO_MS);
    }
    focusEnd();
  }, [busy, disabled, value, generate, version, clearUndo, onGenerated, focusEnd]);

  // Alt+G (Option+G on a Mac, where e.key is "©", so match the physical key) inside the box.
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    const el = inputRef?.current;
    if (!el) return;
    const onKey = (e: Event) => {
      const k = e as KeyboardEvent;
      if (k.altKey && !k.ctrlKey && !k.metaKey && (k.code === "KeyG" || k.key.toLowerCase() === "g")) {
        k.preventDefault();
        void runRef.current();
      }
    };
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, [inputRef]);

  return (
    <span className={`inline-flex flex-wrap items-center gap-2 ${className}`}>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => void run()}
        disabled={disabled || busy}
        aria-label="Generate a sample task"
        title="Generate a sample task (Alt+G)"
        data-testid="generate-task"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
        {busy ? "Generating…" : "Generate"}
      </button>
      {undo && (
        <button
          type="button"
          className="text-xs font-medium text-accent-700 underline underline-offset-2 hover:text-accent-800 dark:text-accent-400"
          onClick={() => {
            onGenerated(undo.previous);
            clearUndo();
            focusEnd();
          }}
        >
          Undo
        </button>
      )}
      {error && (
        <span role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}{" "}
          <button type="button" className="underline underline-offset-2" onClick={clearError}>
            Dismiss
          </button>
        </span>
      )}
    </span>
  );
}
