import { CircleAlert, X } from "lucide-react";
import { Link } from "react-router-dom";

/** A model availability warning from a save (the save itself succeeded), with a link to Settings. */
export default function ModelWarning({ text, onDismiss }: { text: string | null | undefined; onDismiss?: () => void }) {
  if (!text) return null;
  return (
    <div role="alert" data-testid="model-warning" className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">
        {text}{" "}
        <Link to="/settings" className="underline">
          Open Settings
        </Link>
      </span>
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss warning" className="shrink-0 opacity-70 hover:opacity-100">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
