import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Play, Plus, Trash2 } from "lucide-react";
import { api, errMsg, type Agent, type EvalCase, type EvalResults, type Split } from "../../api";
import { Badge, Empty, ErrorBox, Loading, Modal, Section, SplitBadge } from "../../components/ui";
import ImprovePanel from "./ImprovePanel";
import { PassCell, ScoreText } from "./ImprovementView";

function AddCaseForm({ slug, onDone }: { slug: string; onDone: () => void }) {
  const [name, setName] = useState("");
  const [input, setInput] = useState("");
  const [checkerType, setCheckerType] = useState("shell");
  const [checker, setChecker] = useState("");
  const [split, setSplit] = useState<Split>("train");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createEval(slug, { name, input, checker_type: checkerType, checker, split });
      onDone();
    } catch (err) {
      setError(errMsg(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
        <div>
          <label className="label">Name</label>
          <input className="input font-mono" required placeholder="add-refund-endpoint" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label">Split</label>
          <select className="input" value={split} onChange={(e) => setSplit(e.target.value as Split)}>
            <option value="train">train</option>
            <option value="holdout">holdout</option>
          </select>
        </div>
      </div>
      <div>
        <label className="label">Input</label>
        <textarea className="input min-h-[100px]" required value={input} onChange={(e) => setInput(e.target.value)} />
      </div>
      <div>
        <label className="label">Checker</label>
        <div className="mb-2 inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
          {["shell", "judge"].map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setCheckerType(t)}
              className={`rounded-md px-3 py-1 text-sm ${
                checkerType === t ? "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900" : "text-neutral-600 dark:text-neutral-400"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <textarea
          className="input min-h-[80px] font-mono text-xs"
          required
          placeholder={checkerType === "shell" ? "make check && ! grep -rn 'print(' src" : "The answer cites the failing test and explains the fix."}
          value={checker}
          onChange={(e) => setChecker(e.target.value)}
        />
        <p className="mt-1 text-xs text-neutral-500">
          {checkerType === "shell"
            ? "Runs in the final workspace. Exit code 0 means pass."
            : "A rubric for the judge model, graded against the run's output and diff."}
        </p>
      </div>
      <ErrorBox error={error} />
      <div className="flex justify-end">
        <button className="btn-primary" disabled={busy}>
          {busy ? "Adding..." : "Add case"}
        </button>
      </div>
    </form>
  );
}

export default function EvalsTab({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const [cases, setCases] = useState<EvalCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [results, setResults] = useState<EvalResults | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval>>();

  const loadCases = useCallback(async () => {
    try {
      setCases(await api.listEvals(agent.slug));
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [agent.slug]);

  const loadResults = useCallback(async () => {
    try {
      const r = await api.evalResults(agent.slug, agent.prod_version);
      setResults(r);
      return r;
    } catch {
      return null;
    }
  }, [agent.slug, agent.prod_version]);

  // Poll results every 2s until the server reports running: false.
  const startPolling = useCallback(() => {
    const started = Date.now();
    setEvaluating(true);
    clearInterval(poll.current);
    poll.current = setInterval(async () => {
      const r = await loadResults();
      const stillRunning = r ? r.running !== false : true;
      if (!stillRunning || Date.now() - started > 15 * 60 * 1000) {
        clearInterval(poll.current);
        setEvaluating(false);
      }
    }, 2000);
  }, [loadResults]);

  useEffect(() => {
    loadCases();
    loadResults().then((r) => {
      if (r?.running) startPolling();
    });
    return () => clearInterval(poll.current);
  }, [loadCases, loadResults, startPolling]);

  async function runEval() {
    setEvaluating(true);
    setError(null);
    try {
      await api.runEvals(agent.slug, agent.prod_version);
    } catch (e) {
      setError(errMsg(e));
      setEvaluating(false);
      return;
    }
    startPolling();
  }


  async function remove(id: string) {
    if (!confirm("Delete this evaluation case?")) return;
    try {
      await api.deleteEval(id);
      loadCases();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  const resultFor = (caseId: string) => results?.results.find((r) => r.case_id === caseId);

  return (
    <div className="space-y-10">
      <Section
        title="Evaluation cases"
        actions={
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" /> Add case
            </button>
            <button className="btn-primary" onClick={runEval} disabled={evaluating || !cases || cases.length === 0}>
              <Play className="h-4 w-4" /> {evaluating ? "Evaluating..." : `Run evaluation (v${agent.prod_version})`}
            </button>
          </div>
        }
      >
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        {results && (results.train || results.holdout) && (
          <div className="flex flex-wrap gap-3">
            {(["train", "holdout"] as const).map((s) =>
              results[s] ? (
                <div key={s} className="card flex items-center gap-3 px-4 py-2.5">
                  <SplitBadge split={s} />
                  <ScoreText score={results[s]!} />
                  <span className="text-xs text-neutral-500">v{results.version}</span>
                </div>
              ) : null,
            )}
            {evaluating && <span className="self-center text-sm text-blue-600 dark:text-blue-400">Running cases...</span>}
          </div>
        )}
        {cases === null && !error && <Loading />}
        {cases && cases.length === 0 && <Empty>No evaluation cases yet. Add one, or save a run as a test case.</Empty>}
        {cases && cases.length > 0 && (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead className="border-b border-neutral-200 dark:border-neutral-800">
                <tr>
                  <th className="th">Name</th>
                  <th className="th">Split</th>
                  <th className="th">Checker</th>
                  <th className="th">v{agent.prod_version}</th>
                  <th className="th w-10" />
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100 dark:divide-neutral-800">
                {cases.map((c) => {
                  const r = resultFor(c.id);
                  return (
                    <tr key={c.id}>
                      <td className="td">
                        <div className="font-mono text-xs font-medium">{c.name}</div>
                        <div className="mt-0.5 line-clamp-1 max-w-[280px] text-xs text-neutral-500">{c.input}</div>
                      </td>
                      <td className="td">
                        <SplitBadge split={c.split} />
                      </td>
                      <td className="td">
                        <div className="flex items-start gap-2">
                          <Badge>{c.checker_type}</Badge>
                          <code className="line-clamp-2 max-w-[320px] font-mono text-xs text-neutral-600 dark:text-neutral-400">{c.checker}</code>
                        </div>
                      </td>
                      <td className="td">
                        {r ? (
                          <PassCell passed={r.passed} reason={r.reason} runId={r.run_id} />
                        ) : evaluating ? (
                          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                        ) : (
                          <span className="text-xs text-neutral-400">not run</span>
                        )}
                      </td>
                      <td className="td">
                        <button className="btn-ghost btn-sm" onClick={() => remove(c.id)} aria-label={`Delete ${c.name}`}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-neutral-500">
          Train cases guide the optimizer. Holdout cases are never shown to it and decide whether a candidate is trustworthy. Tip: save
          any run as a case from its{" "}
          <Link to={`/agents/${agent.slug}/runs`} className="text-accent-600 hover:underline dark:text-accent-400">
            run page
          </Link>
          .
        </p>
      </Section>

      <ImprovePanel agent={agent} hasCases={!!cases && cases.length > 0} onChanged={onChanged} />

      {adding && (
        <Modal title="Add evaluation case" onClose={() => setAdding(false)} wide>
          <AddCaseForm
            slug={agent.slug}
            onDone={() => {
              setAdding(false);
              loadCases();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
