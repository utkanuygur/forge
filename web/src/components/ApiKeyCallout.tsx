import { KeyRound, X } from "lucide-react";
import { CopyButton } from "./ui";

export default function ApiKeyCallout({ apiKey, title, onDismiss }: { apiKey: string; title: string; onDismiss: () => void }) {
  return (
    <div data-testid="api-key-callout" className="rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">{title}</p>
          <p className="text-sm text-amber-800 dark:text-amber-300">Copy it now. It will not be shown again.</p>
          <div className="flex flex-wrap items-center gap-2">
            <code data-testid="api-key" className="min-w-0 break-all rounded-md bg-white px-2 py-1 font-mono text-xs dark:bg-neutral-900">{apiKey}</code>
            <CopyButton text={apiKey} />
          </div>
        </div>
        <button onClick={onDismiss} aria-label="Dismiss" className="text-amber-700 hover:text-amber-900 dark:text-amber-400">
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
