import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { api, errMsg, slugify, TOOL_NAMES, type AgentCreated, type AgentDraft, type Template } from "../api";
import { ModelField, NetworkField, ToolsField } from "./ConfigFields";
import { DescribeStep, StarterCases, SuggestedSecrets, type DescribeValue, type SecretSuggestion, type StarterCase } from "./DraftFields";
import { ErrorBox, Toggle } from "./ui";

export default function NewAgentForm({ onCreated, onCancel }: { onCreated: (a: AgentCreated) => void; onCancel: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tplKey, setTplKey] = useState("");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>([...TOOL_NAMES]);
  const [network, setNetwork] = useState(false);
  const [autoPromote, setAutoPromote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Drafting: first step "describe", then the form (blank, from a template, or prefilled from a draft).
  const [step, setStep] = useState<"describe" | "form">("describe");
  const [describe, setDescribe] = useState<DescribeValue>({ description: "", examples: [], constraints: "" });
  const [drafting, setDrafting] = useState(false);
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [networkReason, setNetworkReason] = useState("");
  const [secrets, setSecrets] = useState<SecretSuggestion[]>([]);
  const [cases, setCases] = useState<StarterCase[]>([]);

  useEffect(() => {
    api.templates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  function applyTemplate(key: string) {
    setTplKey(key);
    const t = templates.find((x) => x.key === key);
    if (!t) return;
    setName(t.name);
    if (!slugTouched) setSlug(slugify(t.name));
    setDescription(t.description);
    setInstructions(t.system_prompt);
    setTools(t.tools);
    setNetwork(t.network);
  }

  async function draftAgent() {
    setDrafting(true);
    setError(null);
    try {
      const d = await api.draftAgent({
        description: describe.description,
        examples: describe.examples.filter((x) => x.trim()),
        ...(describe.constraints.trim() ? { constraints: describe.constraints.trim() } : {}),
      });
      setDraft(d);
      setTplKey("");
      setName(d.name);
      setSlug(d.slug);
      setSlugTouched(false);
      setDescription(d.description);
      setInstructions(d.system_prompt);
      setTools(d.tools);
      setNetwork(d.network);
      setNetworkReason(d.network_reason);
      setSecrets(d.suggested_secrets.map((x) => ({ ...x, value: "" })));
      setCases(d.eval_cases.map((c) => ({ ...c, selected: true })));
      setStep("form");
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setDrafting(false);
    }
  }

  function startBlank() {
    setDraft(null);
    setCases([]);
    setSecrets([]);
    setError(null);
    setStep("form");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const a = await api.createAgent({
        name,
        slug,
        description,
        system_prompt: instructions,
        ...(model.trim() ? { model: model.trim() } : {}),
        tools,
        network,
        auto_promote: autoPromote,
        ...(draft
          ? {
              eval_cases: cases
                .filter((c) => c.selected)
                .map(({ name, input, checker_type, checker, split }) => ({ name, input, checker_type, checker, split })),
            }
          : {}),
      });
      const values = Object.fromEntries(secrets.filter((x) => x.value).map((x) => [x.name, x.value]));
      if (Object.keys(values).length > 0) {
        try {
          await api.putSecrets(a.slug, values);
        } catch (err) {
          setError(`Agent created, but saving secrets failed: ${errMsg(err)}`);
        }
      }
      onCreated(a);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  if (step === "describe") {
    return (
      <DescribeStep
        value={describe}
        onChange={setDescribe}
        onDraft={draftAgent}
        onBlank={startBlank}
        onCancel={onCancel}
        busy={drafting}
        error={error}
      />
    );
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      {draft && (
        <div data-testid="draft-notice" className="flex flex-wrap items-center gap-3 rounded-lg border border-accent-200 bg-accent-50/60 px-3 py-2.5 text-sm dark:border-accent-900 dark:bg-accent-900/20">
          <span className="flex-1">Drafted from your description. Review and edit everything below; nothing is created until you press Create agent.</span>
          <button type="button" className="btn-secondary btn-sm" onClick={() => setStep("describe")}>
            <ArrowLeft className="h-3.5 w-3.5" /> Edit description
          </button>
        </div>
      )}
      <div hidden={!!draft}>
        <label className="label">Start from</label>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setTplKey("")}
            className={`rounded-lg border px-3 py-1.5 text-sm ${
              tplKey === "" ? "border-accent-500 bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-300" : "border-neutral-300 dark:border-neutral-700"
            }`}
          >
            Blank
          </button>
          {templates.map((t) => (
            <button
              type="button"
              key={t.key}
              title={t.description}
              onClick={() => applyTemplate(t.key)}
              className={`rounded-lg border px-3 py-1.5 text-sm ${
                tplKey === t.key ? "border-accent-500 bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-300" : "border-neutral-300 dark:border-neutral-700"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="name">
            Name
          </label>
          <input
            id="name"
            required
            className="input"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slugTouched) setSlug(slugify(e.target.value));
            }}
          />
        </div>
        <div>
          <label className="label" htmlFor="slug">
            Slug
          </label>
          <input
            id="slug"
            required
            pattern="[a-z0-9][a-z0-9-]*"
            className="input font-mono"
            value={slug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value);
            }}
          />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="desc">
          Description
        </label>
        <input id="desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="instr">
          Instructions
        </label>
        <textarea
          id="instr"
          required
          className="input min-h-[220px] font-mono text-xs leading-relaxed"
          placeholder="You are an agent that..."
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
      </div>
      <ModelField value={model} onChange={setModel} />
      <ToolsField value={tools} onChange={setTools} />
      <div className="space-y-3">
        <NetworkField value={network} onChange={setNetwork} />
        {draft && networkReason && (
          <p data-testid="network-reason" className="-mt-1 pl-12 text-xs text-neutral-600 dark:text-neutral-400">
            Suggested {draft.network ? "on" : "off"}: {networkReason}
          </p>
        )}
        <Toggle
          checked={autoPromote}
          onChange={setAutoPromote}
          label="Auto promote"
          hint="Promote improved versions automatically when they beat production without regressions."
        />
      </div>
      {draft && (
        <>
          {draft.seed_files_hint && (
            <p className="text-xs text-neutral-500">
              <span className="font-medium text-neutral-700 dark:text-neutral-300">Seed files: </span>
              {draft.seed_files_hint}
            </p>
          )}
          <SuggestedSecrets value={secrets} onChange={setSecrets} />
          <StarterCases value={cases} onChange={setCases} />
        </>
      )}
      <ErrorBox error={error} />
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Creating..." : "Create agent"}
        </button>
      </div>
    </form>
  );
}
