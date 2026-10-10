import { useState } from "react";
import { api, errMsg, type Split } from "../api";
import { useToast } from "./Toast";
import { ErrorBox } from "./ui";

export default function SaveAsEval({ runId }: { runId: string }) {
  const toast = useToast();
  const [split, setSplit] = useState<Split>("train");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const ec = await api.saveAsEval(runId, { split, ...(name.trim() ? { name: name.trim() } : {}) });
      toast(`Saved test case "${ec.name}" (${ec.split})`);
      setName("");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Turn this run into an evaluation case. The checker defaults to a judge rubric built from the feedback comment.
      </p>
      <input className="input" aria-label="Case name" placeholder="Case name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
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
        <button className="btn-primary" onClick={save} disabled={busy}>
          {busy ? "Saving..." : "Save as test case"}
        </button>
      </div>
      <ErrorBox error={error} />
    </div>
  );
}
