import { Plus, Sparkles, Trash2 } from "lucide-react";
import type { DraftCase, Split } from "../api";
import { ErrorBox, SplitBadge } from "./ui";

export interface DescribeValue {
  description: string;
  examples: string[];
  constraints: string;
}

/** First step of the New agent form: describe the agent, then draft it or start blank. */
export function DescribeStep({
  value,
  onChange,
  onDraft,
  onBlank,
  onCancel,
  busy,
  error,
}: {
  value: DescribeValue;
  onChange: (v: DescribeValue) => void;
  onDraft: () => void;
  onBlank: () => void;
  onCancel: () => void;
  busy: boolean;
  error: string | null;
}) {
  const setExample = (i: number, text: string) =>
    onChange({ ...value, examples: value.examples.map((e, j) => (j === i ? text : e)) });
  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.description.trim()) onDraft();
      }}
    >
      <div>
        <label className="label" htmlFor="describe">
          Describe what the agent should do
        </label>
        <textarea
          id="describe"
          className="input min-h-[140px]"
          placeholder="For example: read our weekly sales CSV exports and write a one page report for managers with totals by region."
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
        <p className="mt-1 text-xs text-neutral-500">
          Forge drafts the instructions, tools, network setting and a few starter evaluation cases. You review everything before
          anything is created.
        </p>
      </div>
      <div>
        <span className="label">Example tasks (optional)</span>
        <div className="space-y-2">
          {value.examples.map((ex, i) => (
            <div key={i} className="flex gap-2">
              <input
                className="input"
                aria-label={`Example task ${i + 1}`}
                placeholder="A task you would give this agent"
                value={ex}
                onChange={(e) => setExample(i, e.target.value)}
              />
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label={`Delete example ${i + 1}`}
                onClick={() => onChange({ ...value, examples: value.examples.filter((_, j) => j !== i) })}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          <button type="button" className="btn-secondary btn-sm" onClick={() => onChange({ ...value, examples: [...value.examples, ""] })}>
            <Plus className="h-3.5 w-3.5" /> Add example task
          </button>
        </div>
      </div>
      <div>
        <label className="label" htmlFor="constraints">
          Constraints (optional)
        </label>
        <input
          id="constraints"
          className="input"
          placeholder="For example: never edit the input files; no internet access"
          value={value.constraints}
          onChange={(e) => onChange({ ...value, constraints: e.target.value })}
        />
      </div>
      <ErrorBox error={error} />
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="btn-secondary" onClick={onBlank}>
          Start blank
        </button>
        <button type="submit" className="btn-primary" disabled={busy || !value.description.trim()}>
          <Sparkles className="h-4 w-4" /> {busy ? "Drafting..." : "Draft agent"}
        </button>
      </div>
    </form>
  );
}

export interface SecretSuggestion {
  name: string;
  why: string;
  value: string;
}

export function SuggestedSecrets({ value, onChange }: { value: SecretSuggestion[]; onChange: (v: SecretSuggestion[]) => void }) {
  if (value.length === 0) return null;
  return (
    <fieldset data-testid="suggested-secrets">
      <legend className="label">Suggested secrets</legend>
      <ul className="space-y-2">
        {value.map((s, i) => (
          <li key={s.name} className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
            <div className="font-mono text-xs font-medium">{s.name}</div>
            <p className="mt-0.5 text-xs text-neutral-500">{s.why}</p>
            <input
              type="password"
              autoComplete="off"
              className="input mt-2"
              aria-label={`Value for ${s.name}`}
              placeholder="Optional. Leave empty to set it later through the API."
              value={s.value}
              onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
            />
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-neutral-500">Values are stored as agent secrets and reach the sandbox as environment variables.</p>
    </fieldset>
  );
}

export interface StarterCase extends DraftCase {
  selected: boolean;
}

export function StarterCases({ value, onChange }: { value: StarterCase[]; onChange: (v: StarterCase[]) => void }) {
  const set = (i: number, patch: Partial<StarterCase>) => onChange(value.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const selected = value.filter((c) => c.selected).length;
  return (
    <fieldset data-testid="starter-cases">
      <legend className="label">
        Starter evaluation cases ({selected} of {value.length} selected)
      </legend>
      <p className="mb-2 text-xs text-neutral-500">
        Selected cases are created with the agent. Train cases guide the optimizer; holdout cases are never shown to it.
      </p>
      <ul className="space-y-3">
        {value.map((c, i) => (
          <li
            key={i}
            data-testid="starter-case"
            className={`space-y-2 rounded-lg border p-3 ${
              c.selected ? "border-neutral-200 dark:border-neutral-800" : "border-dashed border-neutral-300 opacity-60 dark:border-neutral-700"
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 accent-accent-600"
                aria-label={`Include ${c.name || `case ${i + 1}`}`}
                checked={c.selected}
                onChange={(e) => set(i, { selected: e.target.checked })}
              />
              <input
                className="input max-w-[240px] flex-1 font-mono text-xs"
                aria-label={`Case ${i + 1} name`}
                value={c.name}
                onChange={(e) => set(i, { name: e.target.value })}
              />
              <select className="input w-auto" aria-label={`Case ${i + 1} split`} value={c.split} onChange={(e) => set(i, { split: e.target.value as Split })}>
                <option value="train">train</option>
                <option value="holdout">holdout</option>
              </select>
              <select
                className="input w-auto"
                aria-label={`Case ${i + 1} checker type`}
                value={c.checker_type}
                onChange={(e) => set(i, { checker_type: e.target.value as DraftCase["checker_type"] })}
              >
                <option value="judge">judge</option>
                <option value="shell">shell</option>
              </select>
              <SplitBadge split={c.split} />
            </div>
            <textarea className="input min-h-[60px] text-sm" aria-label={`Case ${i + 1} input`} value={c.input} onChange={(e) => set(i, { input: e.target.value })} />
            <textarea
              className="input min-h-[60px] font-mono text-xs"
              aria-label={`Case ${i + 1} checker`}
              value={c.checker}
              onChange={(e) => set(i, { checker: e.target.value })}
            />
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
