import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Sparkles } from "lucide-react";
import { api, errMsg, type EvalCaseSuggestion, type EvalCasesSuggestion } from "../api";
import { useAssist } from "./AssistButton";
import { Badge, ErrorBox, SplitBadge } from "./ui";

/** One reviewable draft case: a checkbox, name, split, checker type, input preview and an expandable checker. */
function ReviewItem({
  c,
  index,
  checked,
  onToggle,
}: {
  c: EvalCaseSuggestion;
  index: number;
  checked: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);
  const id = `review-case-${index}`;
  return (
    <li data-testid="review-case" data-name={c.name} className="space-y-1.5 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <input id={id} type="checkbox" className="h-4 w-4 accent-accent-600" checked={checked} onChange={onToggle} />
        <label htmlFor={id} className="flex-1 truncate font-mono text-xs font-medium">
          {c.name}
        </label>
        <SplitBadge split={c.split} />
        <Badge>{c.checker_type}</Badge>
      </div>
      <p className="line-clamp-3 pl-6 text-xs text-neutral-600 dark:text-neutral-400">{c.input}</p>
      <div className="pl-6">
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-accent-700 hover:underline dark:text-accent-400"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Checker
        </button>
        {open && (
          <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-950">
            {c.checker}
          </pre>
        )}
        {c.rationale && <p className="mt-1 text-xs text-neutral-500">{c.rationale}</p>}
      </div>
    </li>
  );
}

/** The review list: every draft checked by default; "Add selected" receives the checked ones. */
export function CaseReviewList({
  cases,
  onAdd,
  busy,
}: {
  cases: EvalCaseSuggestion[];
  onAdd: (selected: EvalCaseSuggestion[]) => void;
  busy?: boolean;
}) {
  const [checked, setChecked] = useState<boolean[]>(() => cases.map(() => true));
  const count = checked.filter(Boolean).length;
  return (
    <div className="space-y-3">
      <ul className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
        {cases.map((c, i) => (
          <ReviewItem
            key={`${c.name}-${i}`}
            c={c}
            index={i}
            checked={!!checked[i]}
            onToggle={() => setChecked(checked.map((v, j) => (j === i ? !v : v)))}
          />
        ))}
      </ul>
      <div className="flex justify-end">
        <button
          type="button"
          className="btn-primary"
          disabled={busy || count === 0}
          onClick={() => onAdd(cases.filter((_, i) => checked[i]))}
        >
          {busy ? "Adding..." : `Add selected (${count})`}
        </button>
      </div>
    </div>
  );
}

/** "Generate several": choose how many cases and how many are holdout, review the drafts, add the checked ones. */
export default function GenerateCases({ slug, onAdded }: { slug: string; onAdded: (n: number) => void }) {
  const [n, setN] = useState(3);
  const [holdout, setHoldout] = useState(1);
  const [focus, setFocus] = useState("");
  const [cases, setCases] = useState<EvalCaseSuggestion[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { generate, busy, error: genError } = useAssist<EvalCasesSuggestion>("eval_cases");

  async function run() {
    setError(null);
    const res = await generate({
      slug,
      input: { n, holdout: Math.min(holdout, n), ...(focus.trim() ? { focus: focus.trim() } : {}) },
      resultText: (r) => r.cases.map((c) => c.input).join(" | "),
    });
    if (res) setCases(res.result.cases);
  }

  async function add(selected: EvalCaseSuggestion[]) {
    setAdding(true);
    setError(null);
    let done = 0;
    try {
      for (const c of selected) {
        await api.createEval(slug, { name: c.name, input: c.input, checker_type: c.checker_type, checker: c.checker, split: c.split });
        done++;
      }
      onAdded(done);
    } catch (e) {
      setError(`Added ${done} of ${selected.length}. ${errMsg(e)}`);
      setAdding(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label className="label" htmlFor="gen-n">
            How many cases
          </label>
          <select
            id="gen-n"
            className="input"
            value={n}
            onChange={(e) => {
              const v = Number(e.target.value);
              setN(v);
              setHoldout(Math.min(holdout, v));
            }}
          >
            {[1, 2, 3, 4, 5, 6].map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="gen-holdout">
            Holdout cases
          </label>
          <select id="gen-holdout" className="input" value={holdout} onChange={(e) => setHoldout(Number(e.target.value))}>
            {Array.from({ length: n + 1 }, (_, k) => k).map((k) => (
              <option key={k} value={k}>
                {k} holdout, {n - k} train
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="gen-focus">
            Focus (optional)
          </label>
          <input id="gen-focus" className="input" placeholder="error handling" value={focus} onChange={(e) => setFocus(e.target.value)} />
        </div>
      </div>
      <div className="flex justify-end">
        <button type="button" className="btn-secondary" onClick={() => void run()} disabled={busy || adding}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
          {busy ? "Generating…" : cases ? "Generate again" : "Generate cases"}
        </button>
      </div>
      <ErrorBox error={genError || error} />
      {cases && cases.length > 0 && (
        <CaseReviewList key={cases.map((c) => c.name).join("|")} cases={cases} onAdd={(s) => void add(s)} busy={adding} />
      )}
      {!cases && !busy && <p className="text-xs text-neutral-500">Drafts are only saved when you add them.</p>}
    </div>
  );
}
