import { useCallback, type RefObject } from "react";
import type { TaskSuggestion } from "../api";
import AssistButton, { useAssist } from "./AssistButton";

const taskText = (r: TaskSuggestion) => r.prompt;

/** The assist hook for kind "task": `generate({slug, share, version})` resolves to {result: {title, prompt}}. */
export function useGenerateTask() {
  return useAssist<TaskSuggestion>("task");
}

/**
 * "Generate" for a task box: fills it with a realistic sample task for the agent (assist kind "task").
 *
 * Required: `slug` and `onGenerated(text)` (set the box's value). Optional: `share` (share page token; the server
 * then uses prod and rate limits the link), `value` (the box's text, enables Undo when it is replaced),
 * `inputRef` (focus after fill, Alt+G inside the box), `version` (builder only), `disabled`.
 */
export default function GenerateTaskButton({
  slug,
  onGenerated,
  disabled,
  share,
  value,
  inputRef,
  version,
  className,
}: {
  slug: string;
  onGenerated: (text: string) => void;
  disabled?: boolean;
  share?: string;
  value?: string;
  inputRef?: RefObject<HTMLTextAreaElement | HTMLInputElement>;
  version?: number;
  className?: string;
}) {
  const onResult = useCallback((r: TaskSuggestion) => onGenerated(r.prompt), [onGenerated]);
  return (
    <AssistButton<TaskSuggestion>
      kind="task"
      slug={slug}
      share={share}
      version={version}
      input={value ? { current: value } : undefined}
      resultText={taskText}
      onResult={onResult}
      undoValue={value}
      onUndo={onGenerated}
      inputRef={inputRef}
      ariaLabel="Generate a sample task"
      disabled={disabled}
      className={className}
    />
  );
}
