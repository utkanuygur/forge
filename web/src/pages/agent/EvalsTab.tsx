import { useCallback, useEffect, useRef, useState } from "react";
import { useRefresh } from "../../lib/refresh";
import { Link } from "react-router-dom";
import { Layers, Loader2, Play, Plus, Sparkles, Trash2 } from "lucide-react";
import { api, errMsg, type Agent, type EvalCase, type EvalCaseSuggestion, type EvalResults, type Split } from "../../api";
import AssistButton, { useAssist } from "../../components/AssistButton";
import GenerateCases from "../../components/GenerateCases";
import { useToast } from "../../components/Toast";
import { Badge, Empty, ErrorBox, Loading, Modal, Section, SplitBadge } from "../../components/ui";
import ImprovePanel from "./ImprovePanel";
import { PassCell, ScoreText } from "./ImprovementView";

/** The Add case form. `initial` prefills every field (a generated draft); nothing is saved until Add case. */
export function AddCaseForm({ slug, onDone, initial }: { slug: string; onDone: () => void; initial?: EvalCaseSuggestion }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [input, setInput] = useState(initial?.input ?? "");
  const [checkerType, setCheckerType] = useState<string>(initial?.checker_type ?? "shell");
  const [checker, setChecker] = useState(initial?.checker ?? "");
  const [split, setSplit] = useState<Split>(initial?.split ?? "train");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const checkerRef = useRef<HTMLTextAreaElement>(null);
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
          <label className="label" htmlFor="case-name">Name</label>
          <input id="case-name" className="input font-mono" required placeholder="add-refund-endpoint" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="case-split">Split</label>
          <select id="case-split" className="input" value={split} onChange={(e) => setSplit(e.target.value as Split)}>
            <option value="train">train</option>
            <option value="holdout">holdout</option>
          </select>
        </div>
      </div>
      {initial?.rationale && <p className="text-xs text-neutral-500" data-testid="case-rationale">{initial.rationale}</p>}
      <div>
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
          <label className="label mb-0" htmlFor="case-input">Input</label>
          <AssistButton<EvalCaseSuggestion>
            kind="eval_case"
            slug={slug}
            input={{ split, checker_type: checkerType }}
            resultText={(r) => r.input}
            onResult={(r) => {
              setInput(r.input);
              if (!name.trim()) setName(r.name);
              if (!checker.trim()) {
                setCheckerType(r.checker_type);
                setChecker(r.checker);
              }
            }}
            undoValue={input}
            onUndo={setInput}
            inputRef={inputRef}
            ariaLabel="Generate a task for this case"
          />
        </div>
        <textarea id="case-input" ref={inputRef} className="input min-h-[100px]" required value={input} onChange={(e) => setInput(e.target.value)} />
      </div>
      <div>
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
          <label className="label mb-0" htmlFor="case-checker">Checker</label>
          <AssistButton<EvalCaseSuggestion>
            kind="eval_case"
            slug={slug}
            input={{ task: input, checker_type: checkerType, split }}
            resultText={(r) => r.checker}
            onResult={(r) => {
              setChecker(r.checker);
              if (!name.trim()) setName(r.name);
            }}
            undoValue={checker}
            onUndo={setChecker}
            inputRef={checkerRef}
            disabled={!input.trim()}
            ariaLabel="Generate grading for this case"
          />
        </div>
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
          id="case-checker"
          ref={checkerRef}
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
  const [draft, setDraft] = useState<EvalCaseSuggestion | undefined>(undefined);
  const [several, setSeveral] = useState(false);
  const toast = useToast();
  const gen = useAssist<EvalCaseSuggestion>("eval_case");
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

  // The assistant panel announces changes it made (new cases, an evaluation started).
  useRefresh([`evals:${agent.slug}`], () => {
    loadCases();
    loadResults().then((r) => {
      if (r?.running) startPolling();
    });
  });

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


  async function generateCase() {
    const res = await gen.generate({ slug: agent.slug, resultText: (r) => r.input });
    if (res) {
      setDraft(res.result);
      setAdding(true);
    }
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
          <div className="flex flex-wrap gap-2">
            <button
              className="btn-secondary"
              onClick={() => {
                setDraft(undefined);
                setAdding(true);
              }}
            >
              <Plus className="h-4 w-4" /> Add case
            </button>
            <button className="btn-secondary" onClick={() => void generateCase()} disabled={gen.busy} title="Draft a case for review; nothing is saved until you add it">
              {gen.busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
              {gen.busy ? "Generating…" : "Generate case"}
            </button>
            <button className="btn-secondary" onClick={() => setSeveral(true)}>
              <Layers className="h-4 w-4" /> Generate several
            </button>
            <button className="btn-primary" onClick={runEval} disabled={evaluating || !cases || cases.length === 0}>
              <Play className="h-4 w-4" /> {evaluating ? "Evaluating..." : `Run evaluation (v${agent.prod_version})`}
            </button>
          </div>
        }
      >
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        {results?.error && <ErrorBox error={results.error} />}
        <ErrorBox error={gen.error} onDismiss={gen.clearError} />
        {results && (results.train || results.holdout) && (
          <div className="flex flex-wrap gap-3">
            {(["train", "holdout"] as const).map((s) =>
              results[s] ? (
                <div key={s} data-testid={`eval-score-${s}`} className="card flex items-center gap-3 px-4 py-2.5">
                  <SplitBadge split={s} />
                  <ScoreText score={results[s]!} />
                  <span className="text-xs text-neutral-500">
                    v{results.version}
                    {results.repeats && results.repeats > 1 ? `, majority of ${results.repeats} runs` : ""}
                  </span>
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
                    <tr key={c.id} data-testid="eval-case" data-name={c.name}>
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
                          <PassCell passed={r.passed} reason={r.reason} runId={r.run_id} passes={r.passes} runs={r.runs} />
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

      <ImprovePanel
        agent={agent}
        hasCases={!!cases && cases.length > 0}
        hasTrainCases={!!cases && cases.some((c) => c.split === "train")}
        prodLessons={agent.versions?.find((v) => v.n === agent.prod_version)?.lessons.length ?? 0}
        onChanged={onChanged}
      />

      {several && (
        <Modal title="Generate evaluation cases" onClose={() => setSeveral(false)} wide>
          <GenerateCases
            slug={agent.slug}
            onAdded={(n) => {
              setSeveral(false);
              toast(`Added ${n} case${n === 1 ? "" : "s"}`);
              loadCases();
            }}
          />
        </Modal>
      )}

      {adding && (
        <Modal title={draft ? "Review generated case" : "Add evaluation case"} onClose={() => setAdding(false)} wide>
          <AddCaseForm
            key={draft ? draft.name + draft.input : "blank"}
            initial={draft}
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
