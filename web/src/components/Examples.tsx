// One click example agents (CONTRACT.md "Examples"): the empty state card on the Agents page and the
// "Examples" modal. Uses its own small client so it does not touch api.ts.
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Globe, Sparkles } from "lucide-react";
import { Badge, ErrorBox, Loading, Modal } from "./ui";
import type { AgentCreated } from "../api";

export interface ExampleInfo {
  key: string;
  name: string;
  description: string;
  agent_slug: string;
  installed: boolean;
  eval_cases: number;
  suggested_prompts: number;
  needs_network: boolean;
}

export interface InstalledExample extends AgentCreated {
  example: string;
  created: boolean;
}

export interface InstallAllResult {
  installed: InstalledExample[];
  skipped: string[];
}

export const EXAMPLE_MODELS: { value: string; label: string }[] = [
  { value: "", label: "Server default" },
  { value: "anthropic:claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
  { value: "anthropic:claude-opus-5-5", label: "Claude Opus 5.5" },
  { value: "anthropic:claude-haiku-5-5", label: "Claude Haiku 5.5" },
  { value: "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct", label: "Local Qwen via Ollama (qwen3:4b-instruct)" },
];

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/v1${path}`, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new Error(`Could not reach the Forge server (${(e as Error).message})`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") msg = j.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

const modelBody = (model: string) => (model ? { model } : {});

export const examplesApi = {
  list: () => call<ExampleInfo[]>("GET", "/examples"),
  install: (key: string, model = "") =>
    call<InstalledExample>("POST", `/examples/${encodeURIComponent(key)}/install`, modelBody(model)),
  installAll: (model = "") => call<InstallAllResult>("POST", "/examples/install-all", modelBody(model)),
};

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function installAllMessage(r: InstallAllResult): string {
  const n = r.installed.length;
  if (n === 0) return "All example agents are already installed";
  return `Added ${n} example agent${n === 1 ? "" : "s"}`;
}

/** Primary card shown when there are no agents yet. */
export function ExamplesEmptyCard({
  onInstalledAll,
  onChoose,
}: {
  onInstalledAll: (r: InstallAllResult) => void;
  onChoose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const headingId = useId();

  async function addAll() {
    setBusy(true);
    setError(null);
    try {
      onInstalledAll(await examplesApi.installAll());
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby={headingId}
      data-testid="examples-empty-card"
      className="card flex flex-col gap-4 border-accent-200 p-5 sm:flex-row sm:items-center sm:justify-between dark:border-accent-900"
    >
      <div className="flex min-w-0 items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-50 text-accent-600 dark:bg-accent-900/30 dark:text-accent-400">
          <Sparkles className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h2 id={headingId} className="font-medium">
            Add example agents
          </h2>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Four ready to run agents with test cases: repo maintainer, log triage, data analyst, docs writer.
          </p>
          <div className="mt-2">
            <ErrorBox error={error} onDismiss={() => setError(null)} />
          </div>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <button className="btn-primary" onClick={addAll} disabled={busy} aria-busy={busy}>
          {busy ? "Adding..." : "Add all"}
        </button>
        <button className="btn-secondary" onClick={onChoose} disabled={busy}>
          Choose…
        </button>
      </div>
    </section>
  );
}

/** Lists the shipped examples with a model select and one Add button per example plus Add all. */
export function ExamplesModal({
  onClose,
  onInstalled,
  onInstalledAll,
}: {
  onClose: () => void;
  onInstalled: (a: InstalledExample) => void;
  onInstalledAll: (r: InstallAllResult) => void;
}) {
  const [items, setItems] = useState<ExampleInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const selectId = useId();

  useEffect(() => {
    examplesApi
      .list()
      .then(setItems)
      .catch((e) => setError(errText(e)));
  }, []);

  async function add(key: string) {
    setBusy(key);
    setError(null);
    try {
      onInstalled(await examplesApi.install(key, model));
    } catch (e) {
      setError(errText(e));
      setBusy(null);
    }
  }

  async function addAll() {
    setBusy("*");
    setError(null);
    try {
      onInstalledAll(await examplesApi.installAll(model));
    } catch (e) {
      setError(errText(e));
      setBusy(null);
    }
  }

  const remaining = items?.filter((i) => !i.installed).length ?? 0;

  return (
    <Modal title="Example agents" onClose={onClose} wide>
      <div className="space-y-4">
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Each example comes with its files, test cases and starter prompts, ready to run and improve.
        </p>
        <div>
          <label className="label" htmlFor={selectId}>
            Model
          </label>
          <select id={selectId} className="input" value={model} onChange={(e) => setModel(e.target.value)}>
            {EXAMPLE_MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {model.startsWith("openai_compat:") && (
            <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
              Needs Ollama running locally with the model pulled: ollama pull qwen3:4b-instruct
            </p>
          )}
        </div>
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        {items === null && !error && <Loading />}
        {items && items.length === 0 && (
          <p className="text-sm text-neutral-500">No examples are bundled with this server.</p>
        )}
        {items && items.length > 0 && (
          <ul className="space-y-2" aria-label="Examples">
            {items.map((ex) => (
              <li
                key={ex.key}
                data-testid="example-row"
                data-key={ex.key}
                className="flex flex-col gap-3 rounded-lg border border-neutral-200 p-4 sm:flex-row sm:items-start sm:justify-between dark:border-neutral-800"
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{ex.name}</span>
                    {ex.installed && (
                      <Badge tone="green">
                        <Check className="mr-0.5 h-3 w-3" aria-hidden="true" /> Installed
                      </Badge>
                    )}
                    {ex.needs_network && (
                      <Badge tone="amber">
                        <Globe className="mr-0.5 h-3 w-3" aria-hidden="true" /> Network
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-neutral-600 dark:text-neutral-400">{ex.description}</p>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400">
                    {ex.eval_cases} test case{ex.eval_cases === 1 ? "" : "s"}, {ex.suggested_prompts} starter prompt
                    {ex.suggested_prompts === 1 ? "" : "s"}
                  </p>
                </div>
                {ex.installed ? (
                  <Link
                    className="btn-secondary btn-sm shrink-0 self-start"
                    to={`/agents/${ex.agent_slug}`}
                    aria-label={`Open ${ex.name}`}
                  >
                    Open
                  </Link>
                ) : (
                  <button
                    className="btn-primary btn-sm shrink-0 self-start"
                    onClick={() => add(ex.key)}
                    disabled={busy !== null}
                    aria-label={`Add ${ex.name}`}
                  >
                    {busy === ex.key ? "Adding..." : "Add"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap justify-end gap-2 border-t border-neutral-200 pt-4 dark:border-neutral-800">
          <button className="btn-primary" onClick={addAll} disabled={busy !== null || !items || remaining === 0}>
            {busy === "*" ? "Adding..." : remaining > 0 ? `Add all (${remaining})` : "Add all"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
