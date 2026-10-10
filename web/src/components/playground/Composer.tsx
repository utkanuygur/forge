import { forwardRef, useImperativeHandle, useRef, type ReactNode, type RefObject } from "react";
import { ArrowUp, Square } from "lucide-react";

export interface ComposerHandle {
  focus: () => void;
}

/**
 * Multi-line prompt box. Enter sends, Shift+Enter adds a newline, Up in an empty box recalls the last
 * prompt, Esc stops the running turn. Sending is disabled while a turn runs.
 */
const Composer = forwardRef<
  ComposerHandle,
  {
    value: string;
    onChange: (v: string) => void;
    onSend: (text: string) => void;
    onStop: () => void;
    running: boolean;
    busy?: boolean;
    lastPrompt?: string;
    placeholder?: string;
    /** Placeholder while a turn runs (default "The agent is working…"). */
    runningPlaceholder?: string;
    /** Extra controls for the action row (e.g. Generate); gets the textarea ref for focus and shortcuts. */
    actions?: (inputRef: RefObject<HTMLTextAreaElement>) => ReactNode;
  }
>(function Composer(
  { value, onChange, onSend, onStop, running, busy, lastPrompt, placeholder, runningPlaceholder, actions },
  ref,
) {
  const area = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }), []);
  const locked = running || Boolean(busy);
  const canSend = !locked && value.trim().length > 0;
  const rows = Math.min(8, Math.max(1, value.split("\n").length));

  function send() {
    if (canSend) onSend(value.trim());
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    } else if (e.key === "ArrowUp" && value === "" && lastPrompt) {
      e.preventDefault();
      onChange(lastPrompt);
    } else if (e.key === "Escape" && running) {
      e.preventDefault();
      onStop();
    }
  }

  return (
    <div className="pg-composer" data-testid="composer">
      <div className="flex items-end gap-2">
        <span className="select-none pb-[7px] font-mono text-sm font-semibold text-neutral-400" aria-hidden="true">
          &gt;
        </span>
        <textarea
          ref={area}
          aria-label="Message"
          className="pg-textarea"
          rows={rows}
          value={value}
          disabled={locked}
          placeholder={
            running ? runningPlaceholder ?? "The agent is working…" : placeholder ?? "Ask the agent to do something"
          }
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {running ? (
          <button type="button" className="pg-send pg-stop" onClick={onStop} aria-label="Stop" title="Stop (Esc)">
            <Square className="h-3.5 w-3.5 fill-current" />
          </button>
        ) : (
          <button type="button" className="pg-send" onClick={send} disabled={!canSend} aria-label="Send" title="Send (Enter)">
            <ArrowUp className="h-4 w-4" />
          </button>
        )}
      </div>
      <div className="pg-hints flex flex-wrap items-center gap-2" aria-live="polite">
        {actions && !running && <span className="shrink-0">{actions(area)}</span>}
        {running ? (
          <span data-testid="esc-hint">Esc to stop</span>
        ) : (
          <span>Enter to send · Shift+Enter for a new line · ↑ recalls the last prompt</span>
        )}
      </div>
    </div>
  );
});

export default Composer;
