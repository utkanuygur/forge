import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api, errMsg, type EvalCaseSuggestion, type Split } from "../api";
import { useAssist } from "./AssistButton";
import { useToast } from "./Toast";
import { ErrorBox } from "./ui";

/**
 * Save a run as an evaluation case. "Generate" drafts the name and checker from the run and its feedback (assist
 * kind "eval_case" with from_run_id); the draft is editable and nothing is saved until "Save as test case".
 */
export default function SaveAsEval({ runId, slug }: { runId: string; slug?: string }) {
  const toast = useToast();
  const [split, setSplit] = useState<Split>("train");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<{ checker_type: "shell" | "judge"; checker: string; rationale: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gen = useAssist<EvalCaseSuggestion>("eval_case");

  async function generate() {
    if (!slug) return;
    const res = await gen.generate({ slug, input: { from_run_id: runId, split }, resultText: (r) => r.checker });
    if (!res) return;
    const r = res.result;
    setName(r.name);
    setDraft({ checker_type: r.checker_type, checker: r.checker, rationale: r.rationale });
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const ec = await api.saveAsEval(runId, {
        split,
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(draft && draft.checker.trim() ? { checker_type: draft.checker_type, checker: draft.checker } : {}),
      });
      toast(`Saved test case "${ec.name}" (${ec.split})`);
      setName("");
      setDraft(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Turn this run into an evaluation case. The checker defaults to a judge rubric built from the feedback comment
        {slug ? ", or Generate drafts one from the run and its feedback for you to review." : "."}
      </p>
      <input className="input" aria-label="Case name" placeholder="Case name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
      {draft && (
        <div className="space-y-2" data-testid="generated-checker">
          <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
            {(["shell", "judge"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setDraft({ ...draft, checker_type: t })}
                className={`rounded-md px-3 py-1 text-sm ${
                  draft.checker_type === t ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900" : "text-neutral-600 dark:text-neutral-400"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
          <textarea
            className="input min-h-[120px] font-mono text-xs"
            aria-label="Checker"
            value={draft.checker}
            onChange={(e) => setDraft({ ...draft, checker: e.target.value })}
          />
          {draft.rationale && <p className="text-xs text-neutral-500">{draft.rationale}</p>}
          <button type="button" className="text-xs text-accent-700 underline underline-offset-2 dark:text-accent-400" onClick={() => setDraft(null)}>
            Discard draft
          </button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
          {(["train", "holdout"] as Split[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSplit(s)}
              className={`rounded-md px-3 py-1 text-sm ${
                split === s ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900" : "text-neutral-600 dark:text-neutral-400"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        {slug && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => void generate()}
            disabled={gen.busy || busy}
            aria-label="Generate a test case from this run"
            data-testid="assist-eval_case"
          >
            {gen.busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
            {gen.busy ? "Generating…" : "Generate"}
          </button>
        )}
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy ? "Saving..." : "Save as test case"}
        </button>
      </div>
      <ErrorBox error={error || gen.error} />
    </div>
  );
}
