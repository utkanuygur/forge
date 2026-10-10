import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api, errMsg, type AssistResponse } from "../api";

const UNDO_MS = 10_000;
const AVOID_LIMIT = 10;

export interface AssistOptions<R> {
  slug?: string;
  share?: string;
  version?: number;
  input?: Record<string, unknown>;
  /** The text a result puts into the field; remembered and sent as `avoid` so repeated clicks differ. */
  resultText?: (result: R) => string;
}

/** Calls POST /v1/assist/{kind}. Keeps a loading flag, the last error, and earlier results to avoid. */
export function useAssist<R = Record<string, unknown>>(kind: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<string[]>([]);

  const generate = useCallback(
    async (opts: AssistOptions<R> = {}): Promise<AssistResponse<R> | null> => {
      setBusy(true);
      setError(null);
      try {
        const res = await api.assist<R>(
          kind,
          {
            ...(opts.slug ? { slug: opts.slug } : {}),
            ...(opts.version !== undefined && !opts.share ? { version: opts.version } : {}),
            ...(opts.input ? { input: opts.input } : {}),
            avoid: seen.current.slice(-AVOID_LIMIT),
          },
          opts.share,
        );
        const text = (opts.resultText ?? ((r: R) => JSON.stringify(r)))(res.result);
        if (text) seen.current.push(text);
        return res;
      } catch (e) {
        setError(errMsg(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [kind],
  );

  return { generate, busy, error, clearError: () => setError(null) };
}

type TextInput = HTMLTextAreaElement | HTMLInputElement;

export interface AssistButtonProps<R> extends AssistOptions<R> {
  /** Registered assist kind, e.g. "task". */
  kind: string;
  /** Receives the kind-specific result; write it into the field here. */
  onResult: (result: R, response: AssistResponse<R>) => void;
  /** The field's current text. When it is not empty, Undo is offered for 10 seconds (needs `onUndo`). */
  undoValue?: string;
  /** Restores the previous text. Undo also disappears once the field is edited. */
  onUndo?: (previous: string) => void;
  /** The field: focused after a result (caret at the end, nothing selected); Alt+G (Option+G) inside it generates. */
  inputRef?: RefObject<TextInput>;
  label?: string;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
}

/** A "Generate" button for any assist kind: spinner while busy, Undo for 10 seconds, inline errors, Alt+G. */
export default function AssistButton<R = Record<string, unknown>>({
  kind,
  onResult,
  undoValue,
  onUndo,
  inputRef,
  label = "Generate",
  ariaLabel = "Generate a suggestion",
  disabled,
  className = "",
  ...opts
}: AssistButtonProps<R>) {
  const { generate, busy, error, clearError } = useAssist<R>(kind);
  const [undo, setUndo] = useState<{ previous: string; generated: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resultText = opts.resultText ?? ((r: R) => JSON.stringify(r));

  const clearUndo = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setUndo(null);
  }, []);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  // Editing the generated text makes Undo meaningless.
  useEffect(() => {
    if (undo && undoValue !== undefined && undoValue !== undo.generated) clearUndo();
  }, [undoValue, undo, clearUndo]);

  const focusEnd = useCallback(() => {
    const el = inputRef?.current;
    if (!el) return;
    setTimeout(() => {
      // After React has rendered the new value.
      el.focus();
      const end = el.value.length;
      el.setSelectionRange(end, end);
    }, 0);
  }, [inputRef]);

  const run = async () => {
    if (busy || disabled) return;
    const previous = undoValue ?? "";
    const res = await generate(opts);
    if (!res) return;
    clearUndo();
    onResult(res.result, res);
    const generated = resultText(res.result);
    if (onUndo && previous.trim() && previous !== generated) {
      setUndo({ previous, generated });
      timer.current = setTimeout(() => setUndo(null), UNDO_MS);
    }
    focusEnd();
  };

  // Alt+G (Option+G on a Mac, where e.key is "©", so match the physical key) inside the field.
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
        aria-label={ariaLabel}
        title={`${ariaLabel} (Alt+G)`}
        data-testid={`assist-${kind}`}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
        {busy ? "Generating…" : label}
      </button>
      {undo && (
        <button
          type="button"
          className="text-xs font-medium text-accent-700 underline underline-offset-2 hover:text-accent-800 dark:text-accent-400"
          onClick={() => {
            onUndo?.(undo.previous);
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
