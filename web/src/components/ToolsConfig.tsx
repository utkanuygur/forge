import { useEffect, useRef, useState } from "react";
import AssistButton from "./AssistButton";
import { Bot, Globe, Pencil, Plug, Plus, Trash2 } from "lucide-react";
import {
  api,
  errMsg,
  type Agent,
  type HttpMethod,
  type HttpTool,
  type HttpToolSuggestion,
  type McpServer,
  type McpToolInfo,
  type McpTransport,
  type ToolTestResult,
} from "../api";
import { ErrorBox, Modal } from "./ui";

// ---------------------------------------------------------------- pure helpers (unit tested)

export const DEFAULT_SCHEMA = JSON.stringify(
  { type: "object", properties: { id: { type: "string", description: "The item id." } }, required: ["id"] },
  null,
  2,
);

/** Parses a JSON schema text area. The schema must be a JSON object with "type": "object". */
export function parseSchema(text: string): { schema: Record<string, unknown> | null; error: string | null } {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return { schema: null, error: `Not valid JSON: ${(e as Error).message}` };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { schema: null, error: "The schema must be a JSON object." };
  }
  const obj = value as Record<string, unknown>;
  if (obj.type !== "object") return { schema: null, error: 'The schema needs "type": "object".' };
  if (obj.properties !== undefined && (typeof obj.properties !== "object" || Array.isArray(obj.properties))) {
    return { schema: null, error: '"properties" must be an object.' };
  }
  return { schema: obj, error: null };
}

/** Parses one `KEY: value` (or `KEY=value`) pair per line. Blank lines are ignored. */
export function parsePairs(text: string, sep: ":" | "=" = ":"): { pairs: Record<string, string>; error: string | null } {
  const pairs: Record<string, string> = {};
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const at = line.indexOf(sep);
    if (at <= 0) return { pairs, error: `Line ${i + 1} needs the form NAME${sep === ":" ? ": " : "="}value.` };
    pairs[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return { pairs, error: null };
}

export function formatPairs(pairs: Record<string, string> | undefined, sep: ":" | "=" = ":"): string {
  return Object.entries(pairs || {})
    .map(([k, v]) => (sep === ":" ? `${k}: ${v}` : `${k}=${v}`))
    .join("\n");
}

/** URL template placeholders such as {id}; secret references like {{NAME}} are not placeholders. */
export function urlPlaceholders(url: string): string[] {
  return [...url.replace(/\{\{[^}]*\}\}/g, "").matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map((m) => m[1]);
}

/** Example arguments for a schema: one value per property, typed after the schema. */
export function sampleArgs(schema: Record<string, unknown> | null): Record<string, unknown> {
  const props = (schema?.properties || {}) as Record<string, { type?: string }>;
  const out: Record<string, unknown> = {};
  for (const [k, p] of Object.entries(props)) {
    const t = p?.type;
    out[k] = t === "integer" || t === "number" ? 1 : t === "boolean" ? true : t === "array" ? [] : t === "object" ? {} : "example";
  }
  return out;
}

// ---------------------------------------------------------------- HTTP tool editor

const METHODS: HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/** The form fields an http_tool suggestion fills, serialized so Undo can restore them all at once. */
function httpToolSnapshot(f: { name: string; description: string; method: string; url: string; headers: string; query: string; schemaText: string }) {
  return JSON.stringify(f);
}

export function HttpToolModal({
  slug,
  network,
  initial,
  onSave,
  onClose,
  existing = [],
}: {
  slug: string;
  network: boolean;
  initial: HttpTool | null;
  onSave: (t: HttpTool) => void;
  onClose: () => void;
  existing?: string[];
}) {
  const [source, setSource] = useState("");
  const sourceRef = useRef<HTMLTextAreaElement>(null);
  const [name, setName] = useState(initial?.name || "");
  const [description, setDescription] = useState(initial?.description || "");
  const [method, setMethod] = useState<HttpMethod>(initial?.method || "GET");
  const [url, setUrl] = useState(initial?.url || "");
  const [headers, setHeaders] = useState(formatPairs(initial?.headers));
  const [query, setQuery] = useState((initial?.query_params || []).join(", "));
  const [timeout, setTimeoutS] = useState(String(initial?.timeout ?? 30));
  const [schemaText, setSchemaText] = useState(initial ? JSON.stringify(initial.parameters, null, 2) : DEFAULT_SCHEMA);
  const { schema, error: schemaError } = parseSchema(schemaText);
  const [argsText, setArgsText] = useState(() => JSON.stringify(sampleArgs(parseSchema(schemaText).schema), null, 2));
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const parsedHeaders = parsePairs(headers, ":");
  const props = Object.keys((schema?.properties as Record<string, unknown>) || {});
  const missing = urlPlaceholders(url).filter((p) => !props.includes(p));
  const problems = [
    !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name) && name ? "Name must start with a letter and use letters, digits, - or _." : null,
    url && !/^https?:\/\//i.test(url) ? "URL must start with http:// or https://." : null,
    missing.length ? `URL placeholder ${missing.map((m) => `{${m}}`).join(", ")} is not a parameter.` : null,
    schemaError,
    parsedHeaders.error ? `Headers: ${parsedHeaders.error}` : null,
  ].filter(Boolean) as string[];
  const valid = !!name && !!url && !!description.trim() && problems.length === 0;

  function build(): HttpTool {
    return {
      name: name.trim(),
      description: description.trim(),
      method,
      url: url.trim(),
      headers: parsedHeaders.pairs,
      parameters: schema || { type: "object", properties: {} },
      query_params: query
        .split(",")
        .map((q) => q.trim())
        .filter(Boolean),
      timeout: Number(timeout) || 30,
    };
  }

  async function test() {
    setError(null);
    setResult(null);
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsText || "{}");
    } catch (e) {
      setError(`Sample arguments are not valid JSON: ${(e as Error).message}`);
      return;
    }
    setTesting(true);
    try {
      setResult(await api.testTool(slug, { tool: build(), args, network }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setTesting(false);
    }
  }

  const snapshot = httpToolSnapshot({ name, description, method, url, headers, query, schemaText });
  function restore(text: string) {
    try {
      const f = JSON.parse(text);
      setName(f.name);
      setDescription(f.description);
      setMethod(f.method);
      setUrl(f.url);
      setHeaders(f.headers);
      setQuery(f.query);
      setSchemaText(f.schemaText);
    } catch {
      /* not a snapshot */
    }
  }
  const fromSuggestion = (r: HttpToolSuggestion) =>
    httpToolSnapshot({
      name: r.name,
      description: r.description,
      method: r.method,
      url: r.url,
      headers: formatPairs(r.headers),
      query: (r.query_params || []).join(", "),
      schemaText: JSON.stringify(r.parameters, null, 2),
    });

  return (
    <Modal title={initial ? `Edit ${initial.name}` : "Add HTTP tool"} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
          {/* Not a <label>: "curl" would make getByLabel("URL") ambiguous. */}
          <p className="label">Describe the endpoint, paste a curl command or API doc</p>
          <textarea
            id="http-source"
            aria-label="Endpoint source"
            ref={sourceRef}
            className="input min-h-[70px] font-mono text-xs"
            placeholder="curl -H 'Authorization: Bearer $TOKEN' https://api.example.com/orders/123"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <AssistButton<HttpToolSuggestion>
              kind="http_tool"
              slug={slug}
              input={{ source, existing }}
              resultText={fromSuggestion}
              onResult={(r) => {
                restore(fromSuggestion(r));
                setArgsText(JSON.stringify(sampleArgs(r.parameters), null, 2));
              }}
              undoValue={snapshot}
              onUndo={restore}
              inputRef={sourceRef}
              disabled={!source.trim()}
              ariaLabel="Generate the tool from this text"
            />
            <span className="text-xs text-neutral-500">Fills the fields below. Secrets stay as {"{{NAME}}"} references.</span>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="http-name">
              Name
            </label>
            <input id="http-name" className="input font-mono" placeholder="get_order" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="http-method">
              Method
            </label>
            <select id="http-method" className="input" value={method} onChange={(e) => setMethod(e.target.value as HttpMethod)}>
              {METHODS.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="http-description">
            Description
          </label>
          <input
            id="http-description"
            className="input"
            placeholder="What the tool does and when to use it"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="http-url">
            URL
          </label>
          <input
            id="http-url"
            className="input font-mono"
            placeholder="https://api.example.com/orders/{id}"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <p className="mt-1 text-xs text-neutral-500">
            Use {"{param}"} for values from the arguments. Other arguments go to the query string for GET and DELETE, and to
            a JSON body otherwise.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="http-headers">
              Headers
            </label>
            <textarea
              id="http-headers"
              className="input min-h-[72px] font-mono text-xs"
              placeholder={"Authorization: Bearer {{API_KEY}}"}
              value={headers}
              onChange={(e) => setHeaders(e.target.value)}
            />
            <p className="mt-1 text-xs text-neutral-500">One per line. {"{{NAME}}"} inserts the agent secret NAME.</p>
          </div>
          <div className="space-y-4">
            <div>
              <label className="label" htmlFor="http-query">
                Always in query string
              </label>
              <input id="http-query" className="input font-mono" placeholder="page, limit" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="http-timeout">
                Timeout (seconds)
              </label>
              <input id="http-timeout" className="input" type="number" min={1} max={120} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
            </div>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="http-schema">
            Parameters (JSON schema)
          </label>
          <textarea
            id="http-schema"
            className={`input min-h-[160px] font-mono text-xs ${schemaError ? "border-red-400 dark:border-red-700" : ""}`}
            value={schemaText}
            onChange={(e) => setSchemaText(e.target.value)}
            aria-invalid={!!schemaError}
          />
        </div>
        {problems.length > 0 && (
          <ul className="space-y-1 text-sm text-red-700 dark:text-red-400" data-testid="http-tool-problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        <div className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
          <label className="label" htmlFor="http-args">
            Sample arguments
          </label>
          <textarea id="http-args" className="input min-h-[72px] font-mono text-xs" value={argsText} onChange={(e) => setArgsText(e.target.value)} />
          <div className="mt-2 flex items-center gap-3">
            <button className="btn-secondary btn-sm" disabled={!valid || testing} onClick={test}>
              {testing ? "Testing..." : "Test"}
            </button>
            {!network && <span className="text-xs text-amber-700 dark:text-amber-400">Network access is off, so the test will be refused.</span>}
          </div>
          {result && (
            <div className="mt-3" data-testid="http-test-result">
              <div className={`mb-1 text-xs font-medium ${result.is_error ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
                {result.is_error ? "Failed" : "Succeeded"}
                {result.status ? ` with HTTP ${result.status}` : ""} in {result.duration_ms} ms
              </div>
              <pre className="mono-block max-h-64">{result.output}</pre>
            </div>
          )}
        </div>
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={!valid} onClick={() => onSave(build())}>
            Save tool
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- MCP server editor

function McpServerModal({
  slug,
  network,
  seedFiles,
  initial,
  onSave,
  onClose,
}: {
  slug: string;
  network: boolean;
  seedFiles: Record<string, string>;
  initial: McpServer | null;
  onSave: (s: McpServer) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial?.name || "");
  const [transport, setTransport] = useState<McpTransport>(initial?.transport || "http");
  const [url, setUrl] = useState(initial?.url || "");
  const [headers, setHeaders] = useState(formatPairs(initial?.headers));
  const [command, setCommand] = useState(initial?.command || "");
  const [args, setArgs] = useState((initial?.args || []).join("\n"));
  const [env, setEnv] = useState(formatPairs(initial?.env, "="));
  const [timeout, setTimeoutS] = useState(String(initial?.timeout ?? 60));
  const [tools, setTools] = useState<McpToolInfo[] | null>(null);
  const [listing, setListing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remote = transport !== "stdio";
  const parsedHeaders = parsePairs(headers, ":");
  const parsedEnv = parsePairs(env, "=");
  const problems = [
    name && !/^[A-Za-z0-9](?:[A-Za-z0-9-]|_(?!_)){0,31}$/.test(name) ? "Name must use letters, digits, - or a single _." : null,
    remote && url && !/^https?:\/\//i.test(url) ? "URL must start with http:// or https://." : null,
    remote && parsedHeaders.error ? `Headers: ${parsedHeaders.error}` : null,
    !remote && parsedEnv.error ? `Environment: ${parsedEnv.error}` : null,
  ].filter(Boolean) as string[];
  const valid = !!name && (remote ? !!url.trim() : !!command.trim()) && problems.length === 0;

  function build(): McpServer {
    const base = { name: name.trim(), transport, timeout: Number(timeout) || 60 };
    if (remote) return { ...base, url: url.trim(), headers: parsedHeaders.pairs };
    return {
      ...base,
      command: command.trim(),
      args: args
        .split("\n")
        .map((a) => a.trim())
        .filter(Boolean),
      env: parsedEnv.pairs,
    };
  }

  async function list() {
    setError(null);
    setTools(null);
    setListing(true);
    try {
      const res = await api.listMcpTools(slug, { server: build(), network, seed_files: seedFiles });
      setTools(res.tools);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setListing(false);
    }
  }

  return (
    <Modal title={initial ? `Edit ${initial.name}` : "Add MCP server"} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="mcp-name">
              Name
            </label>
            <input id="mcp-name" className="input font-mono" placeholder="docs" value={name} onChange={(e) => setName(e.target.value)} />
            <p className="mt-1 text-xs text-neutral-500">Tools appear as mcp__{name || "name"}__tool.</p>
          </div>
          <div>
            <label className="label" htmlFor="mcp-transport">
              Type
            </label>
            <select id="mcp-transport" className="input" value={transport} onChange={(e) => setTransport(e.target.value as McpTransport)}>
              <option value="http">Remote (Streamable HTTP)</option>
              <option value="sse">Remote (SSE)</option>
              <option value="stdio">Local command in the sandbox (stdio)</option>
            </select>
          </div>
        </div>
        {remote ? (
          <>
            <div>
              <label className="label" htmlFor="mcp-url">
                URL
              </label>
              <input
                id="mcp-url"
                className="input font-mono"
                placeholder={transport === "sse" ? "https://example.com/sse" : "https://example.com/mcp"}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              {!network && <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">Remote servers need network access.</p>}
            </div>
            <div>
              <label className="label" htmlFor="mcp-headers">
                Headers
              </label>
              <textarea
                id="mcp-headers"
                className="input min-h-[72px] font-mono text-xs"
                placeholder={"Authorization: Bearer {{DOCS_TOKEN}}"}
                value={headers}
                onChange={(e) => setHeaders(e.target.value)}
              />
              <p className="mt-1 text-xs text-neutral-500">One per line. {"{{NAME}}"} inserts the agent secret NAME.</p>
            </div>
          </>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="mcp-command">
                  Command
                </label>
                <input id="mcp-command" className="input font-mono" placeholder="python" value={command} onChange={(e) => setCommand(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="mcp-args">
                  Arguments (one per line)
                </label>
                <textarea id="mcp-args" className="input min-h-[72px] font-mono text-xs" placeholder="server.py" value={args} onChange={(e) => setArgs(e.target.value)} />
              </div>
            </div>
            <div>
              <label className="label" htmlFor="mcp-env">
                Environment
              </label>
              <textarea
                id="mcp-env"
                className="input min-h-[72px] font-mono text-xs"
                placeholder={"TOKEN={{DOCS_TOKEN}}"}
                value={env}
                onChange={(e) => setEnv(e.target.value)}
              />
              <p className="mt-1 text-xs text-neutral-500">
                One NAME=value per line. The server runs inside each run's sandbox, in the workspace, with the agent's network policy.
              </p>
            </div>
          </>
        )}
        <div className="max-w-[200px]">
          <label className="label" htmlFor="mcp-timeout">
            Call timeout (seconds)
          </label>
          <input id="mcp-timeout" className="input" type="number" min={1} max={600} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
        </div>
        {problems.length > 0 && (
          <ul className="space-y-1 text-sm text-red-700 dark:text-red-400">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <div className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
          <button className="btn-secondary btn-sm" disabled={!valid || listing} onClick={list}>
            {listing ? "Connecting..." : "List tools"}
          </button>
          {tools && (
            <div className="mt-3" data-testid="mcp-tools">
              {tools.length === 0 ? (
                <p className="text-sm text-neutral-500">The server offers no tools.</p>
              ) : (
                <ul className="space-y-1.5">
                  {tools.map((t) => (
                    <li key={t.name} className="text-sm">
                      <span className="font-mono text-xs">{t.name}</span>
                      {t.description && <span className="text-neutral-500"> {t.description}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <ErrorBox error={error} onDismiss={() => setError(null)} />
        <div className="flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={!valid} onClick={() => onSave(build())}>
            Save server
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- the section

function ItemRow({
  icon,
  title,
  detail,
  onEdit,
  onRemove,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="flex items-center gap-2 px-3 py-2" data-testid="tool-item" data-name={title}>
      {icon}
      <button className="min-w-0 flex-1 text-left" onClick={onEdit}>
        <span className="block truncate font-mono text-xs hover:underline">{title}</span>
        <span className="block truncate text-xs text-neutral-500">{detail}</span>
      </button>
      <button className="btn-ghost btn-sm" aria-label={`Edit ${title}`} onClick={onEdit}>
        <Pencil className="h-3.5 w-3.5" />
      </button>
      <button className="btn-ghost btn-sm" aria-label={`Remove ${title}`} onClick={onRemove}>
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}

export default function ToolsConfig({
  agent,
  network,
  seedFiles,
  httpTools,
  onHttpTools,
  mcpServers,
  onMcpServers,
  callableAgents,
  onCallableAgents,
}: {
  agent: Agent;
  network: boolean;
  seedFiles: Record<string, string>;
  httpTools: HttpTool[];
  onHttpTools: (v: HttpTool[]) => void;
  mcpServers: McpServer[];
  onMcpServers: (v: McpServer[]) => void;
  callableAgents: string[];
  onCallableAgents: (v: string[]) => void;
}) {
  const [editingTool, setEditingTool] = useState<{ index: number; tool: HttpTool | null } | null>(null);
  const [editingServer, setEditingServer] = useState<{ index: number; server: McpServer | null } | null>(null);
  const [others, setOthers] = useState<Agent[] | null>(null);

  useEffect(() => {
    api
      .listAgents()
      .then((all) => setOthers(all.filter((a) => a.slug !== agent.slug)))
      .catch(() => setOthers([]));
  }, [agent.slug]);

  const list = "divide-y divide-neutral-200 rounded-lg border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800";

  return (
    <div className="space-y-5" data-testid="tools-config">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="label mb-0">HTTP tools</span>
          <button className="btn-secondary btn-sm" onClick={() => setEditingTool({ index: -1, tool: null })}>
            <Plus className="h-3.5 w-3.5" /> Add HTTP tool
          </button>
        </div>
        {httpTools.length === 0 ? (
          <p className="text-sm text-neutral-500">No HTTP tools. Add one to let the agent call an API with typed arguments.</p>
        ) : (
          <ul className={list}>
            {httpTools.map((t, i) => (
              <ItemRow
                key={`${t.name}-${i}`}
                icon={<Globe className="h-4 w-4 shrink-0 text-neutral-400" />}
                title={t.name}
                detail={`${t.method} ${t.url}`}
                onEdit={() => setEditingTool({ index: i, tool: t })}
                onRemove={() => onHttpTools(httpTools.filter((_, j) => j !== i))}
              />
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="label mb-0">MCP servers</span>
          <button className="btn-secondary btn-sm" onClick={() => setEditingServer({ index: -1, server: null })}>
            <Plus className="h-3.5 w-3.5" /> Add MCP server
          </button>
        </div>
        {mcpServers.length === 0 ? (
          <p className="text-sm text-neutral-500">No MCP servers. Their tools are offered to the agent as mcp__server__tool.</p>
        ) : (
          <ul className={list}>
            {mcpServers.map((s, i) => (
              <ItemRow
                key={`${s.name}-${i}`}
                icon={<Plug className="h-4 w-4 shrink-0 text-neutral-400" />}
                title={s.name}
                detail={s.transport === "stdio" ? `stdio: ${[s.command, ...(s.args || [])].join(" ")}` : `${s.transport}: ${s.url}`}
                onEdit={() => setEditingServer({ index: i, server: s })}
                onRemove={() => onMcpServers(mcpServers.filter((_, j) => j !== i))}
              />
            ))}
          </ul>
        )}
      </div>

      <fieldset>
        <legend className="label">Callable agents</legend>
        {others === null ? (
          <p className="text-sm text-neutral-500">Loading agents...</p>
        ) : others.length === 0 ? (
          <p className="text-sm text-neutral-500">No other agents yet.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {others.map((a) => (
              <label
                key={a.slug}
                className="flex cursor-pointer items-start gap-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm dark:border-neutral-800"
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-accent-600"
                  checked={callableAgents.includes(a.slug)}
                  onChange={(e) =>
                    onCallableAgents(e.target.checked ? [...callableAgents, a.slug] : callableAgents.filter((s) => s !== a.slug))
                  }
                  aria-label={`Allow calling ${a.slug}`}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 font-medium">
                    <Bot className="h-3.5 w-3.5 text-neutral-400" /> {a.name}
                  </span>
                  <span className="block truncate font-mono text-xs text-neutral-500">agent__{a.slug}</span>
                </span>
              </label>
            ))}
          </div>
        )}
        <p className="mt-1.5 text-xs text-neutral-500">
          Each call starts a run of that agent's production version and returns its answer. Calls nest at most 3 levels and
          never loop back to an agent already in the chain.
        </p>
      </fieldset>

      {editingTool && (
        <HttpToolModal
          slug={agent.slug}
          existing={httpTools.map((t) => t.name).filter((n) => n !== editingTool.tool?.name)}
          network={network}
          initial={editingTool.tool}
          onClose={() => setEditingTool(null)}
          onSave={(t) => {
            const next = [...httpTools];
            if (editingTool.index >= 0) next[editingTool.index] = t;
            else next.push(t);
            onHttpTools(next);
            setEditingTool(null);
          }}
        />
      )}
      {editingServer && (
        <McpServerModal
          slug={agent.slug}
          network={network}
          seedFiles={seedFiles}
          initial={editingServer.server}
          onClose={() => setEditingServer(null)}
          onSave={(s) => {
            const next = [...mcpServers];
            if (editingServer.index >= 0) next[editingServer.index] = s;
            else next.push(s);
            onMcpServers(next);
            setEditingServer(null);
          }}
        />
      )}
    </div>
  );
}
