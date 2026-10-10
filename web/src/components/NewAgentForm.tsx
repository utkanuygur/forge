import { useEffect, useState } from "react";
import { api, errMsg, slugify, TOOL_NAMES, type AgentCreated, type Template } from "../api";
import { ModelField, NetworkField, ToolsField } from "./ConfigFields";
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
      });
      onCreated(a);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
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
        <Toggle
          checked={autoPromote}
          onChange={setAutoPromote}
          label="Auto promote"
          hint="Promote improved versions automatically when they beat production without regressions."
        />
      </div>
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
