import { useEffect, useMemo, useState } from "react";
import { FileText, FlaskConical, Plus, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useOpenPlayground } from "../../components/playground/Playground";
import { api, errMsg, fmtTime, type Agent, type ApprovalPolicy, type HttpTool, type LessonMeta, type McpServer, type Version } from "../../api";
import PolicyEditor, { MonthlyBudgetSetting, RunCapField, normalizePolicy, parseUsd } from "../../components/PolicyEditor";
import { ModelField, NetworkField, ToolsField } from "../../components/ConfigFields";
import ToolsConfig from "../../components/ToolsConfig";
import { useToast } from "../../components/Toast";
import { Badge, CreatedByBadge, Empty, ErrorBox, Modal, Section, Toggle } from "../../components/ui";

function SeedFileModal({
  initialPath,
  initialContent,
  onSave,
  onClose,
}: {
  initialPath: string;
  initialContent: string;
  onSave: (path: string, content: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [content, setContent] = useState(initialContent);
  return (
    <Modal title={initialPath ? "Edit seed file" : "Add seed file"} onClose={onClose} wide>
      <div className="space-y-4">
        <div>
          <label className="label">Path</label>
          <input className="input font-mono" placeholder="src/config.yaml" value={path} onChange={(e) => setPath(e.target.value)} />
        </div>
        <div>
          <label className="label">Content</label>
          <textarea className="input min-h-[280px] font-mono text-xs" value={content} onChange={(e) => setContent(e.target.value)} />
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={!path.trim()} onClick={() => onSave(path.trim(), content)}>
            Save file
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function fmtAge(ts: number | undefined, now = Date.now() / 1000): string {
  if (!ts) return "";
  const s = Math.max(0, now - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  if (s < 129600) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function LessonOrigin({ meta }: { meta: LessonMeta | undefined }) {
  const parts: string[] = [];
  if (meta?.created_in_version) {
    const who = meta.origin === "human" ? "by hand" : "by the optimizer";
    parts.push(`${meta.op === "edit" ? "Edited" : "Added"} in v${meta.created_in_version} ${who}`);
    const age = fmtAge(meta.created_at);
    if (age) parts.push(age);
  } else {
    parts.push("Origin not recorded");
  }
  const runs = meta?.evidence_run_ids ?? [];
  const p = meta?.pruning;
  return (
    <div data-testid="lesson-origin" className="mt-0.5 space-y-0.5 text-xs text-neutral-500">
      <div className="flex flex-wrap items-center gap-x-1.5">
        <span>{parts.join(" · ")}</span>
        {runs.length > 0 && <span>· evidence</span>}
        {runs.slice(0, 3).map((id) => (
          <Link key={id} to={`/runs/${id}`} className="font-mono text-accent-600 hover:underline dark:text-accent-400">
            {id}
          </Link>
        ))}
      </div>
      {p && (
        <div data-testid="lesson-pruning" data-verdict={p.verdict} className={p.verdict === "unhelpful" ? "text-amber-700 dark:text-amber-400" : "text-emerald-700 dark:text-emerald-400"}>
          {p.verdict === "unhelpful" ? "Pruning found no measurable effect: " : "Kept after pruning: "}
          {p.evidence}
        </div>
      )}
    </div>
  );
}

export default function ConfigureTab({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const toast = useToast();
  const openPlayground = useOpenPlayground(agent.slug);
  const versions = useMemo(() => [...(agent.versions || [])].sort((a, b) => b.n - a.n), [agent.versions]);
  const prod = versions.find((v) => v.n === agent.prod_version);

  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [network, setNetwork] = useState(false);
  const [seeds, setSeeds] = useState<Record<string, string>>({});
  const [httpTools, setHttpTools] = useState<HttpTool[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [callable, setCallable] = useState<string[]>([]);
  const [rationale, setRationale] = useState("");
  const [policy, setPolicy] = useState<ApprovalPolicy>({});
  const [runCap, setRunCap] = useState("");
  const [editing, setEditing] = useState<{ path: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset(v: Version | undefined) {
    if (!v) return;
    setPrompt(v.system_prompt);
    setModel(v.model);
    setTools(v.tools);
    setNetwork(v.network);
    setSeeds(v.seed_files || {});
    setHttpTools(v.http_tools || []);
    setMcpServers(v.mcp_servers || []);
    setCallable(v.callable_agents || []);
    setRationale("");
    setPolicy(v.approval_policy || {});
    setRunCap(v.max_run_cost_usd == null ? "" : String(v.max_run_cost_usd));
  }
  useEffect(() => reset(prod), [prod?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty =
    !!prod &&
    (prompt !== prod.system_prompt ||
      model !== prod.model ||
      network !== prod.network ||
      JSON.stringify([...tools].sort()) !== JSON.stringify([...prod.tools].sort()) ||
      JSON.stringify(seeds) !== JSON.stringify(prod.seed_files || {}) ||
      JSON.stringify(normalizePolicy(policy)) !== JSON.stringify(normalizePolicy(prod.approval_policy)) ||
      parseUsd(runCap) !== (prod.max_run_cost_usd ?? null) ||
      JSON.stringify(httpTools) !== JSON.stringify(prod.http_tools || []) ||
      JSON.stringify(mcpServers) !== JSON.stringify(prod.mcp_servers || []) ||
      JSON.stringify(callable) !== JSON.stringify(prod.callable_agents || []));
  const capInvalid = parseUsd(runCap) === undefined;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const v = await api.createVersion(agent.slug, {
        system_prompt: prompt,
        model,
        tools,
        network,
        seed_files: seeds,
        http_tools: httpTools,
        mcp_servers: mcpServers,
        callable_agents: callable,
        rationale,
        approval_policy: normalizePolicy(policy),
        max_run_cost_usd: parseUsd(runCap) ?? null,
      });
      toast(`Saved v${v.n}. Promote it to put it in production.`);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function promote(n: number) {
    setError(null);
    try {
      await api.promote(agent.slug, n);
      toast(`v${n} is now in production`);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function setEvalRepeats(v: number) {
    try {
      await api.patchAgent(agent.slug, { eval_repeats: v });
      toast(`Eval cases now run ${v} time${v === 1 ? "" : "s"}`);
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  async function setAutoPromote(v: boolean) {
    try {
      await api.patchAgent(agent.slug, { auto_promote: v });
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  if (!prod) return <ErrorBox error={`Production version v${agent.prod_version} was not found.`} />;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-8">
        <Section title={`Editing from production v${prod.n}`}>
          <div className="card space-y-5 p-5">
            <div>
              <label className="label" htmlFor="instructions">
                Instructions
              </label>
              <textarea
                id="instructions"
                className="input min-h-[300px] font-mono text-xs leading-relaxed"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </div>
            <div>
              <div className="mb-1.5 flex items-center gap-2">
                <span className="label mb-0">Learned lessons</span>
                <CreatedByBadge by={prod.created_by} />
              </div>
              {prod.lessons.length === 0 ? (
                <p className="text-sm text-neutral-500">No lessons yet. Run Improve on the Evaluations tab to learn some.</p>
              ) : (
                <ol className="list-decimal space-y-1.5 rounded-lg bg-neutral-50 py-3 pl-8 pr-4 text-sm dark:bg-neutral-950">
                  {prod.lessons.map((l, i) => (
                    <li key={i} data-testid="lesson" className="text-neutral-700 dark:text-neutral-300">
                      <span>{l}</span>
                      <LessonOrigin meta={prod.lesson_meta?.[l]} />
                    </li>
                  ))}
                </ol>
              )}
              <p className="mt-1.5 text-xs text-neutral-500">Lessons are written by the optimizer and carried into new versions.</p>
            </div>
            <ModelField value={model} onChange={setModel} />
            <ToolsField value={tools} onChange={setTools} />
            <NetworkField value={network} onChange={setNetwork} />
            <section aria-label="Tools" className="space-y-3 border-t border-neutral-200 pt-5 dark:border-neutral-800">
              <div>
                <h3 className="text-sm font-semibold">Tools</h3>
                <p className="text-xs text-neutral-500">HTTP endpoints, MCP servers and other agents the model can call.</p>
              </div>
              <ToolsConfig
                agent={agent}
                network={network}
                seedFiles={seeds}
                httpTools={httpTools}
                onHttpTools={setHttpTools}
                mcpServers={mcpServers}
                onMcpServers={setMcpServers}
                callableAgents={callable}
                onCallableAgents={setCallable}
              />
            </section>
            <PolicyEditor value={policy} onChange={setPolicy} network={network} />
            <RunCapField value={runCap} onChange={setRunCap} />
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="label mb-0">Seed files</span>
                <button className="btn-secondary btn-sm" onClick={() => setEditing({ path: "", content: "" })}>
                  <Plus className="h-3.5 w-3.5" /> Add file
                </button>
              </div>
              {Object.keys(seeds).length === 0 ? (
                <p className="text-sm text-neutral-500">No seed files. Add files that every run's workspace should start with.</p>
              ) : (
                <ul className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
                  {Object.entries(seeds).map(([path, content]) => (
                    <li key={path} className="flex items-center gap-2 px-3 py-2">
                      <FileText className="h-4 w-4 text-neutral-400" />
                      <button className="flex-1 truncate text-left font-mono text-xs hover:underline" onClick={() => setEditing({ path, content })}>
                        {path}
                      </button>
                      <span className="text-xs text-neutral-400">{content.length} chars</span>
                      <button
                        className="btn-ghost btn-sm"
                        aria-label={`Remove ${path}`}
                        onClick={() => {
                          const next = { ...seeds };
                          delete next[path];
                          setSeeds(next);
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="border-t border-neutral-200 pt-5 dark:border-neutral-800">
              <label className="label" htmlFor="rationale">
                What changed?
              </label>
              <input
                id="rationale"
                className="input"
                placeholder="Short note for the version history"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
              />
            </div>
            <ErrorBox error={error} onDismiss={() => setError(null)} />
            <div className="flex justify-end gap-2">
              <button className="btn-secondary" disabled={!dirty || busy} onClick={() => reset(prod)}>
                Discard
              </button>
              <button
                className="btn-secondary"
                disabled={busy}
                title={dirty ? "Try these unsaved edits in the Playground" : "Open the Playground on production"}
                onClick={() => openPlayground(dirty ? { system_prompt: prompt, model, tools, network, seed_files: seeds } : undefined)}
              >
                <FlaskConical className="h-4 w-4" /> Test
              </button>
              <button className="btn-primary" disabled={!dirty || busy || capInvalid} onClick={save}>
                {busy ? "Saving..." : "Save as new version"}
              </button>
            </div>
          </div>
        </Section>
      </div>

      <div className="space-y-8">
        <Section title="Settings">
          <div className="card p-4">
            <Toggle
              checked={agent.auto_promote}
              onChange={setAutoPromote}
              label="Auto promote"
              hint="Eligible improvements go to production automatically."
            />
            <div className="mt-4 border-t border-neutral-100 pt-4 dark:border-neutral-800">
              <label className="label" htmlFor="eval-repeats">
                Eval repeats
              </label>
              <select
                id="eval-repeats"
                className="input"
                value={agent.eval_repeats ?? 3}
                onChange={(e) => setEvalRepeats(Number(e.target.value))}
              >
                {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-neutral-500">Each eval case runs this many times per version and passes on a strict majority.</p>
            </div>
            <div className="mt-4 border-t border-neutral-200 pt-4 dark:border-neutral-800">
              <MonthlyBudgetSetting agent={agent} onChanged={onChanged} />
            </div>
          </div>
        </Section>
        <Section title="Version history">
          {versions.length === 0 ? (
            <Empty>No versions.</Empty>
          ) : (
            <ul className="space-y-2">
              {versions.map((v) => (
                <li key={v.id} className="card p-3.5" data-testid="version-item" data-version={v.n}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">v{v.n}</span>
                    <CreatedByBadge by={v.created_by} />
                    {v.n === agent.prod_version && <Badge tone="green">Production</Badge>}
                    <div className="flex-1" />
                    {v.n !== agent.prod_version && (
                      <button className="btn-secondary btn-sm" onClick={() => promote(v.n)}>
                        Promote
                      </button>
                    )}
                  </div>
                  {v.rationale && <p className="mt-1.5 text-sm text-neutral-600 dark:text-neutral-400">{v.rationale}</p>}
                  <p className="mt-1 text-xs text-neutral-400">
                    {fmtTime(v.created_at)} · {v.lessons.length} lessons
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      {editing && (
        <SeedFileModal
          initialPath={editing.path}
          initialContent={editing.content}
          onClose={() => setEditing(null)}
          onSave={(path, content) => {
            const next = { ...seeds };
            if (editing.path && editing.path !== path) delete next[editing.path];
            next[path] = content;
            setSeeds(next);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}
