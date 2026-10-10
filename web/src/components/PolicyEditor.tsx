import { useEffect, useRef, useState } from "react";
import { Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { api, errMsg, type Agent, type ApprovalPolicy, type ApprovalRulesSuggestion, type BudgetSuggestion, type PolicyRule } from "../api";
import AssistButton, { useAssist } from "./AssistButton";

export const DEFAULT_TIMEOUT_SECONDS = 15 * 60;
const FILE_TOOLS = ["write_file", "edit_file"];

/** Presets mirror forge/policy.py PRESETS. `network` presets are offered only when network access is on. */
export const PRESETS: { key: string; label: string; network?: boolean; rules: PolicyRule[] }[] = [
  {
    key: "rm_rf",
    label: "rm -rf",
    rules: [{ tool: "bash", pattern: "\\brm\\s+(-\\w+\\s+)*-\\w*([rR]\\w*f|f\\w*[rR])", mode: "ask", label: "rm -rf" }],
  },
  { key: "git_push", label: "git push", rules: [{ tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask", label: "git push" }] },
  {
    key: "curl_wget",
    label: "curl or wget",
    network: true,
    rules: [{ tool: "bash", pattern: "\\b(curl|wget)\\b", mode: "ask", label: "curl or wget" }],
  },
  {
    key: "package_installs",
    label: "Package installs",
    rules: [
      {
        tool: "bash",
        pattern:
          "\\b(pip3?\\s+install|uv\\s+(pip\\s+install|add)|npm\\s+(install|i|add)|pnpm\\s+(add|install)|yarn\\s+add" +
          "|apt(-get)?\\s+install|brew\\s+install|cargo\\s+(install|add)|gem\\s+install)\\b",
        mode: "ask",
        label: "package installs",
      },
    ],
  },
  {
    key: "env_files",
    label: "Writes to *.env",
    rules: FILE_TOOLS.map((tool) => ({ tool, pattern: "*.env", mode: "ask" as const, label: "env files" })),
  },
  {
    key: "migrations",
    label: "Writes to migrations/**",
    rules: FILE_TOOLS.map((tool) => ({ tool, pattern: "migrations/**", mode: "ask" as const, label: "migrations" })),
  },
];

const TOOL_OPTIONS = [
  { value: "bash", label: "bash" },
  { value: "write_file", label: "write_file" },
  { value: "edit_file", label: "edit_file" },
  { value: "read_file", label: "read_file" },
  { value: "*", label: "any tool" },
];

/** Canonical form for comparing policies (matches what the server stores). */
export function normalizePolicy(p: ApprovalPolicy | undefined | null): Required<ApprovalPolicy> {
  return {
    rules: (p?.rules || []).map((r) => ({
      tool: r.tool,
      pattern: r.pattern && r.pattern.trim() ? r.pattern : null,
      mode: r.mode,
      label: r.label || null,
    })),
    timeout_seconds: p?.timeout_seconds ?? DEFAULT_TIMEOUT_SECONDS,
  };
}

function sameRule(a: PolicyRule, b: PolicyRule): boolean {
  return a.tool === b.tool && (a.pattern || null) === (b.pattern || null) && a.mode === b.mode;
}

/** Appends suggested rules (assist kind "approval_rules"), skipping any that already exist. */
export function addSuggestedRules(policy: ApprovalPolicy, suggested: ApprovalRulesSuggestion["rules"]): ApprovalPolicy {
  const rules = [...(policy.rules || [])];
  for (const s of suggested) {
    const r: PolicyRule = { tool: s.tool, pattern: s.pattern || null, mode: s.mode, label: s.label || null };
    if (!rules.some((x) => sameRule(x, r))) rules.push(r);
  }
  return { ...policy, rules };
}

/** "Suggest rules": asks for approval rules fitting the agent's tools and network; they are appended, with Undo. */
function SuggestRules({
  slug,
  value,
  onChange,
  network,
  tools,
  httpTools,
}: {
  slug: string;
  value: ApprovalPolicy;
  onChange: (p: ApprovalPolicy) => void;
  network: boolean;
  tools: string[];
  httpTools: string[];
}) {
  const { generate, busy, error } = useAssist<ApprovalRulesSuggestion>("approval_rules");
  const [state, setState] = useState<{ previous: ApprovalPolicy; applied: ApprovalPolicy; explanation: string } | null>(null);
  async function run() {
    const res = await generate({
      slug,
      input: { tools, network, http_tools: httpTools, current: normalizePolicy(value) },
      resultText: (r) => r.rules.map((x) => `${x.tool} ${x.pattern}`).join(", "),
    });
    if (!res) return;
    const next = addSuggestedRules(value, res.result.rules);
    onChange(next);
    setState({ previous: value, applied: next, explanation: res.result.explanation });
  }
  const canUndo = state && state.applied === value;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => void run()}
          disabled={busy}
          aria-label="Suggest approval rules"
          data-testid="assist-approval_rules"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Sparkles className="h-3.5 w-3.5" aria-hidden />}
          {busy ? "Generating…" : "Suggest rules"}
        </button>
        {canUndo && (
          <button
            type="button"
            className="text-xs font-medium text-accent-700 underline underline-offset-2 dark:text-accent-400"
            onClick={() => {
              onChange(state.previous);
              setState(null);
            }}
          >
            Undo
          </button>
        )}
      </div>
      {state && <p className="text-xs text-neutral-500" data-testid="rules-explanation">{state.explanation}</p>}
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

/** Adds a preset's rules, skipping any that already exist. */
export function addPreset(policy: ApprovalPolicy, key: string): ApprovalPolicy {
  const preset = PRESETS.find((p) => p.key === key);
  if (!preset) return policy;
  const rules = [...(policy.rules || [])];
  for (const r of preset.rules) if (!rules.some((x) => sameRule(x, r))) rules.push({ ...r });
  return { ...policy, rules };
}

export default function PolicyEditor({
  value,
  onChange,
  network,
  slug,
  tools = [],
  httpTools = [],
}: {
  value: ApprovalPolicy;
  onChange: (p: ApprovalPolicy) => void;
  network: boolean;
  /** Enables "Suggest rules" (assist kind "approval_rules"). */
  slug?: string;
  tools?: string[];
  httpTools?: string[];
}) {
  const rules = value.rules || [];
  const setRules = (next: PolicyRule[]) => onChange({ ...value, rules: next });
  const update = (i: number, patch: Partial<PolicyRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const minutes = Math.round(((value.timeout_seconds ?? DEFAULT_TIMEOUT_SECONDS) / 60) * 100) / 100;
  const [minutesText, setMinutesText] = useState(String(minutes));
  useEffect(() => setMinutesText(String(minutes)), [minutes]);

  return (
    <fieldset data-testid="policy-editor" className="space-y-3">
      <legend className="label">Approval rules</legend>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Matching tool calls pause until someone approves them (Ask) or are refused outright (Deny). Bash patterns are
        regular expressions searched in the command. File patterns are path globs such as <code>*.env</code> or{" "}
        <code>migrations/**</code>. Leave the pattern empty to match every call to the tool.
      </p>
      {rules.length === 0 ? (
        <p className="text-sm text-neutral-500">No rules. Every tool call runs without asking.</p>
      ) : (
        <ul className="space-y-2">
          {rules.map((r, i) => (
            <li
              key={i}
              data-testid="policy-rule"
              className="grid gap-2 rounded-lg border border-neutral-200 p-2 dark:border-neutral-800 sm:grid-cols-[130px_minmax(0,1fr)_100px_auto]"
            >
              <select
                aria-label={`Rule ${i + 1} tool`}
                className="input"
                value={r.tool}
                onChange={(e) => update(i, { tool: e.target.value, pattern: e.target.value === "*" ? null : r.pattern })}
              >
                {[...TOOL_OPTIONS, ...(TOOL_OPTIONS.some((o) => o.value === r.tool) ? [] : [{ value: r.tool, label: r.tool }])].map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <input
                aria-label={`Rule ${i + 1} pattern`}
                className="input font-mono text-xs"
                disabled={r.tool === "*"}
                placeholder={r.tool === "bash" ? "regex, e.g. \\bgit\\s+push\\b" : r.tool === "*" ? "every call" : "glob, e.g. *.env"}
                value={r.pattern || ""}
                onChange={(e) => update(i, { pattern: e.target.value })}
              />
              <select
                aria-label={`Rule ${i + 1} mode`}
                className="input"
                value={r.mode}
                onChange={(e) => update(i, { mode: e.target.value as PolicyRule["mode"] })}
              >
                <option value="ask">Ask</option>
                <option value="deny">Deny</option>
              </select>
              <button
                type="button"
                className="btn-ghost btn-sm justify-self-end"
                aria-label={`Remove rule ${i + 1}`}
                onClick={() => setRules(rules.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => setRules([...rules, { tool: "bash", pattern: "", mode: "ask" }])}
        >
          <Plus className="h-3.5 w-3.5" /> Add rule
        </button>
        <span className="ml-1 text-xs text-neutral-500">Presets:</span>
        {PRESETS.filter((p) => !p.network || network).map((p) => (
          <button
            key={p.key}
            type="button"
            className="rounded-md border border-neutral-200 px-2 py-0.5 text-xs text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:text-neutral-400"
            onClick={() => onChange(addPreset(value, p.key))}
          >
            {p.label}
          </button>
        ))}
      </div>
      {slug && <SuggestRules slug={slug} value={value} onChange={onChange} network={network} tools={tools} httpTools={httpTools} />}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label htmlFor="approval-timeout">Deny automatically after</label>
        <input
          id="approval-timeout"
          type="number"
          min={0.05}
          step="any"
          className="input w-24"
          value={minutesText}
          onChange={(e) => {
            setMinutesText(e.target.value);
            const m = Number(e.target.value);
            if (m > 0) onChange({ ...value, timeout_seconds: Math.max(1, Math.round(m * 60)) });
          }}
        />
        <span>minutes without a decision</span>
      </div>
    </fieldset>
  );
}

/** Parses a USD input: empty means no limit (null); invalid or negative values return undefined. */
export function parseUsd(text: string): number | null | undefined {
  const t = text.trim().replace(/^\$/, "");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

/** Explanation line under a budget field after Generate (assist kind "budget"). */
function BudgetExplanation({ text }: { text: string | null }) {
  return text ? (
    <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400" data-testid="budget-explanation">
      {text}
    </p>
  ) : null;
}

const usd = (n: number) => String(Math.round(n * 100) / 100);

export function RunCapField({ value, onChange, slug }: { value: string; onChange: (v: string) => void; slug?: string }) {
  const invalid = parseUsd(value) === undefined;
  const ref = useRef<HTMLInputElement>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  return (
    <div>
      <label className="label" htmlFor="run-cap">
        Spending cap per run (USD)
      </label>
      <div className="flex flex-wrap items-center gap-2">
      <input
        id="run-cap"
        ref={ref}
        className="input w-40"
        inputMode="decimal"
        placeholder="No cap"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {slug && (
        <AssistButton<BudgetSuggestion>
          kind="budget"
          slug={slug}
          input={{}}
          resultText={(r) => usd(r.max_run_cost_usd)}
          onResult={(r) => {
            onChange(usd(r.max_run_cost_usd));
            setExplanation(r.explanation);
          }}
          undoValue={value}
          onUndo={onChange}
          inputRef={ref}
          ariaLabel="Suggest a per run cap"
        />
      )}
      </div>
      <BudgetExplanation text={explanation} />
      <p className={`mt-1 text-xs ${invalid ? "text-red-600" : "text-neutral-500"}`}>
        {invalid
          ? "Enter a dollar amount, or leave empty for no cap."
          : "A run that goes over this estimated cost stops with status budget exceeded."}
      </p>
    </div>
  );
}

export function MonthlyBudgetSetting({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const initial = agent.monthly_budget_usd == null ? "" : String(agent.monthly_budget_usd);
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(initial), [initial]);
  const parsed = parseUsd(text);
  const dirty = text.trim() !== initial;

  async function save() {
    if (parsed === undefined) return;
    setBusy(true);
    setError(null);
    try {
      await api.patchAgent(agent.slug, { monthly_budget_usd: parsed });
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <label className="label" htmlFor="monthly-budget">
        Monthly budget (USD)
      </label>
      <div className="flex gap-2">
        <input
          id="monthly-budget"
          className="input"
          inputMode="decimal"
          placeholder="No budget"
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <AssistButton<BudgetSuggestion>
          kind="budget"
          slug={agent.slug}
          input={{}}
          resultText={(r) => usd(r.monthly_budget_usd)}
          onResult={(r) => {
            setText(usd(r.monthly_budget_usd));
            setExplanation(r.explanation);
          }}
          undoValue={text}
          onUndo={setText}
          inputRef={ref}
          label="Suggest"
          ariaLabel="Suggest a monthly budget"
        />
        <button className="btn-secondary" disabled={!dirty || busy || parsed === undefined} onClick={save}>
          {busy ? "Saving..." : "Save"}
        </button>
      </div>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Once this month's estimated spend reaches the budget, new runs and evaluations are refused. Evaluation runs
        count toward it.
      </p>
      <BudgetExplanation text={explanation} />
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
