// Typed client for the Forge HTTP API. Shapes mirror CONTRACT.md.

export type RunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "timeout"
  // governance
  | "awaiting_approval"
  | "budget_exceeded";
export type Split = "train" | "holdout";
export type CreatedBy = "human" | "optimizer";
export type ImprovementStatus = "running" | "promoted" | "eligible" | "rejected" | "failed";
export type EventType =
  | "run_started"
  | "assistant"
  | "tool_call"
  | "tool_result"
  | "run_finished"
  | "error"
  // governance
  | "approval_requested"
  | "approval_resolved"
  | "budget_warning";

export type ApprovalMode = "ask" | "deny";

export interface PolicyRule {
  tool: string;
  pattern?: string | null;
  mode: ApprovalMode;
  label?: string | null;
}

export interface ApprovalPolicy {
  rules?: PolicyRule[];
  timeout_seconds?: number;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
  rule: PolicyRule & { index: number; description: string };
  status: "pending" | "approved" | "denied" | "expired";
  note: string;
  resolved_by: string | null;
  created_at: number;
  expires_at: number;
  resolved_at: number | null;
}

export interface Governance {
  monthly_budget_usd: number | null;
  month_to_date_usd: number;
  fraction: number | null;
  state: "none" | "ok" | "warning" | "exceeded";
  pending_approvals: number;
  awaiting_runs: string[];
}

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface HttpTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  url: string;
  method: HttpMethod;
  headers: Record<string, string>;
  query_params?: string[];
  timeout?: number;
  max_response_chars?: number;
}

export type McpTransport = "http" | "sse" | "stdio";

export interface McpServer {
  name: string;
  transport: McpTransport;
  url?: string;
  headers?: Record<string, string>;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  timeout?: number;
}

export interface McpToolInfo {
  name: string;
  tool: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface ToolTestResult {
  output: string;
  is_error: boolean;
  status: number | null;
  duration_ms: number;
}

export interface Version {
  id: string;
  agent_id: string;
  n: number;
  system_prompt: string;
  lessons: string[];
  model: string;
  tools: string[];
  http_tools: HttpTool[];
  mcp_servers?: McpServer[];
  callable_agents?: string[];
  network: boolean;
  seed_files: Record<string, string>;
  parent_id: string | null;
  created_by: CreatedBy;
  rationale: string;
  created_at: number;
  // Per-lesson provenance keyed by lesson text (see CONTRACT.md "Lesson provenance").
  lesson_meta?: Record<string, LessonMeta>;
  approval_policy?: ApprovalPolicy;
  max_run_cost_usd?: number | null;
}

export interface PruneNote {
  improvement_id: string;
  verdict: "unhelpful" | "helpful" | "unknown";
  evidence: string;
  at: number;
  version?: number;
}

export interface LessonMeta {
  created_in_version?: number;
  evidence_run_ids?: string[];
  origin?: "optimizer" | "human";
  op?: "add" | "edit";
  created_at?: number;
  edited_from?: string;
  pruning?: PruneNote;
}

export interface Agent {
  id: string;
  slug: string;
  name: string;
  description: string;
  prod_version: number;
  auto_promote: boolean;
  eval_repeats?: number; // runs per eval case (k); a case passes on a strict majority
  share_token: string;
  created_at: number;
  versions?: Version[];
  monthly_budget_usd?: number | null;
  month_to_date_usd?: number;
  budget_state?: Governance["state"];
  pending_approvals?: number;
}

export interface AgentCreated extends Agent {
  api_key: string;
}

export interface Feedback {
  id: string;
  run_id: string;
  rating: 1 | -1;
  comment: string;
  source: string;
  created_at: number;
}

export interface Run {
  id: string;
  agent_id: string;
  version_id: string;
  version_n: number;
  source: string;
  input: string;
  status: RunStatus;
  output: string;
  error: string | null;
  turns: number;
  tokens_in: number;
  tokens_out: number;
  // Prompt-cache usage (not included in tokens_in, not priced in cost_usd).
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  has_artifacts: boolean;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  feedback: Feedback[];
  eval_case_id?: string | null;
  parent_run_id?: string | null;
  child_usage?: { tokens_in: number; tokens_out: number; cost_usd: number };
  // Not in CONTRACT.md; used if the backend includes it.
  agent_slug?: string;
  // Observability and governance (additive): the stored run cost; null means the model's price is unknown.
  duration_ms?: number | null;
  cost_usd?: number | null;
  cost_note?: string;
}

// ---------------------------------------------------------------- observability

export interface Delta {
  value: number | null;
  previous: number | null;
  change: number | null;
  change_pct: number | null;
}

export interface Health {
  runs: number;
  finished: number;
  succeeded: number;
  failed: number;
  timeout: number;
  budget_exceeded?: number;
  in_progress: number;
  awaiting_approval?: number;
  success_rate: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_turns: number | null;
  feedback_count: number;
  thumbs_up: number;
  thumbs_down: number;
  thumbs_up_rate: number | null;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cost_usd: number;
  cost_per_run: number | null;
  unpriced_runs: number;
}

export type KpiKey = "runs" | "success_rate" | "p95_ms" | "cost_usd" | "thumbs_up_rate";

export interface SeriesPoint {
  t: number;
  runs: number;
  succeeded: number;
  failed: number;
  timeout: number;
  budget_exceeded?: number;
  in_progress: number;
  cost_usd: number;
  success_rate: number | null;
}

export interface Price {
  model: string | null;
  kind: "metered" | "self_hosted" | "unknown";
  input_per_mtok: number | null;
  output_per_mtok: number | null;
  note: string;
}

export interface FailureEntry {
  category: string;
  label: string;
  reason?: string;
  runs: number;
  count: number;
  example_run_ids: string[];
}

export interface FailureBreakdown {
  runs: number;
  categories: FailureEntry[];
  reasons: FailureEntry[];
}

export interface ToolStat {
  name: string;
  calls: number;
  errors: number;
  error_rate: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  total_ms: number;
}

export interface SlowCall {
  run_id: string;
  seq: number;
  name: string;
  duration_ms: number;
  is_error: boolean;
  summary: string;
  version_n: number | null;
}

export interface EvalScore {
  passed: number;
  total: number;
  evaluated: number;
  pass_rate: number | null;
}

export interface VersionHealth {
  version: number;
  model: string;
  created_by: string;
  created_at: number;
  is_prod: boolean;
  is_previous_prod: boolean;
  promoted_at: number | null;
  price: Price;
  period_runs: number;
  health: Health;
  eval: { train: EvalScore; holdout: EvalScore } | null;
  delta_vs_previous_prod?: Record<string, Delta>;
}

export interface Promotion {
  version: number | null;
  from_version: number | null;
  source: string;
  at: number;
}

export interface ImprovementCost {
  id: string;
  status: string;
  base_version: number | null;
  candidate_version: number | null;
  created_at: number;
  ended_at: number | null;
  eval_runs: number;
  eval_cost_usd: number;
  judge_cost_usd: number;
  optimizer_cost_usd: number;
  grader_calls: number;
  tokens_in: number;
  tokens_out: number;
  total_cost_usd: number;
  cost_complete: boolean;
}

export interface AgentMetrics {
  agent: string;
  days: number;
  include_eval: boolean;
  since: number;
  until: number;
  bucket: "hour" | "day";
  prod_version: number | null;
  previous_prod_version: number | null;
  summary: Health;
  previous: Health;
  kpis: Record<KpiKey, Delta>;
  series: SeriesPoint[];
  promotions: Promotion[];
  versions: VersionHealth[];
  cost: {
    total_usd: number;
    unpriced_runs: number;
    versions: { version: number; model: string; price: Price; runs: number; tokens_in: number; tokens_out: number; cache_read_tokens?: number; cache_write_tokens?: number; cost_usd: number | null; cost_per_run: number | null }[];
    improvements: ImprovementCost[];
  };
  failures: { all: FailureBreakdown; versions: (FailureBreakdown & { version: number })[] };
  tools: { tools: ToolStat[]; slowest: SlowCall[]; versions: { version: number; tools: ToolStat[] }[] };
  slowest_runs: { id: string; version: number; status: RunStatus; source: string; duration_ms: number; input: string; cost_usd: number | null; created_at: number }[];
}

export interface OverviewAgent {
  slug: string;
  name: string;
  prod_version: number | null;
  health: Health;
  failing_runs: number;
  flagged_runs: number;
  last_run_at: number | null;
  spark: number[];
}

export interface Overview {
  days: number;
  include_eval: boolean;
  since: number;
  until: number;
  bucket: "hour" | "day";
  summary: Health;
  previous: Health;
  kpis: Record<KpiKey, Delta>;
  series: SeriesPoint[];
  agents: OverviewAgent[];
  top_failure_reasons: (FailureEntry & { agent: string; agent_name: string })[];
  top_failing_agents: { slug: string; name: string; flagged_runs: number; failing_runs: number }[];
}

export interface Timeline {
  run_id: string;
  status: RunStatus;
  estimated: boolean;
  total_ms: number;
  model_ms: number;
  tool_ms: number;
  approval_ms?: number; // governance: approval waits, excluded from tool_ms
  other_ms: number;
  queue_ms: number | null;
  turns: number;
  tool_calls: number;
  spans: import("./lib/timeline").TimelineSpan[];
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cost_usd: number | null;
  cost_note: string;
  price: Price;
}

export interface ForgeEvent {
  run_id: string;
  seq: number;
  ts: number;
  type: EventType;
  payload: Record<string, unknown>;
}

export interface EvalCase {
  id: string;
  agent_id: string;
  name: string;
  input: string;
  seed_files: Record<string, string>;
  checker_type: string;
  checker: string;
  split: Split;
  created_at: number;
}

export interface Score {
  passed: number;
  total: number;
}

export interface CaseResult {
  passed: boolean; // majority verdict over `runs`
  reason: string;
  run_id: string | null;
  passes?: number;
  runs?: number;
}

export interface ImprovementCase {
  case_id: string;
  name: string;
  split: Split;
  base: CaseResult | null;
  candidate: CaseResult | null;
}

export interface LessonDiff {
  op: "add" | "edit" | "remove";
  text: string;
  evidence_run_ids: string[];
  rationale: string;
  // Not in CONTRACT.md: previous text for "edit" ops, if provided.
  old_text?: string;
  index?: number;
}

export interface Improvement {
  id: string;
  agent_id: string;
  status: ImprovementStatus;
  base_version: number;
  candidate_version: number | null;
  base: { train: Score; holdout: Score } | null;
  candidate: { train: Score; holdout: Score } | null;
  cases: ImprovementCase[];
  newly_passing: string[];
  regressions: string[];
  lessons_diff: LessonDiff[];
  error: string | null;
  created_at: number;
  ended_at: number | null;
  kind?: "improve" | "prune";
  ablations?: Ablation[];
}

export interface Ablation {
  lesson: string;
  version: number | null;
  verdict: "unhelpful" | "helpful" | "unknown" | "not_ablated";
  evidence: string;
  base_train?: Score;
  train?: Score;
  run_ids?: string[];
}

export interface DraftCase {
  name: string;
  input: string;
  checker_type: "judge" | "shell";
  checker: string;
  split: Split;
}

export interface AgentDraft {
  name: string;
  slug: string;
  description: string;
  system_prompt: string;
  tools: string[];
  network: boolean;
  network_reason: string;
  suggested_secrets: { name: string; why: string }[];
  seed_files_hint: string;
  eval_cases: DraftCase[];
}

export interface EvalResults {
  version: number;
  running?: boolean;
  train?: Score;
  holdout?: Score;
  repeats?: number;
  results: ({ case_id: string } & CaseResult)[];
}

export interface Template {
  key: string;
  name: string;
  description: string;
  system_prompt: string;
  tools: string[];
  network: boolean;
}

export interface CreateAgentBody {
  slug: string;
  name: string;
  description?: string;
  system_prompt: string;
  model?: string;
  tools?: string[];
  network?: boolean;
  seed_files?: Record<string, string>;
  auto_promote?: boolean;
  eval_cases?: NewEvalBody[];
}

export interface NewVersionBody {
  system_prompt?: string;
  lessons?: string[];
  model?: string;
  tools?: string[];
  network?: boolean;
  seed_files?: Record<string, string>;
  http_tools?: HttpTool[];
  mcp_servers?: McpServer[];
  callable_agents?: string[];
  rationale?: string;
  approval_policy?: ApprovalPolicy;
  max_run_cost_usd?: number | null;
}

export interface NewEvalBody {
  name: string;
  input: string;
  seed_files?: Record<string, string>;
  checker_type: string;
  checker: string;
  split: Split;
}

// ---------------------------------------------------------------- transport

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function withShare(path: string, share?: string): string {
  if (!share) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}share=${encodeURIComponent(share)}`;
}

async function parseError(res: Response): Promise<ApiError> {
  let msg = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") msg = body.detail;
    else if (body && body.detail) msg = JSON.stringify(body.detail);
  } catch {
    /* non JSON error */
  }
  return new ApiError(res.status, msg);
}

async function request<T>(method: string, path: string, body?: unknown, share?: string): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (share) headers["X-Share-Token"] = share;
  let res: Response;
  try {
    res = await fetch(withShare(`/v1${path}`, share), { method, headers, body: payload });
  } catch (e) {
    throw new ApiError(0, `Could not reach the Forge server (${(e as Error).message})`);
  }
  if (!res.ok) throw await parseError(res);
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

/** GET returning the JSON list and the X-Total-Count header. */
async function requestWithTotal<T>(path: string, share?: string): Promise<{ items: T[]; total: number }> {
  const headers: Record<string, string> = {};
  if (share) headers["X-Share-Token"] = share;
  let res: Response;
  try {
    res = await fetch(withShare(`/v1${path}`, share), { method: "GET", headers });
  } catch (e) {
    throw new ApiError(0, `Could not reach the Forge server (${(e as Error).message})`);
  }
  if (!res.ok) throw await parseError(res);
  const items = (await res.json()) as T[];
  const total = Number(res.headers.get("x-total-count"));
  return { items, total: Number.isFinite(total) && res.headers.has("x-total-count") ? total : items.length };
}

const enc = encodeURIComponent;

// ---------------------------------------------------------------- endpoints

export const api = {
  // builder
  listAgents: () => request<Agent[]>("GET", "/agents"),
  getAgent: (slug: string, share?: string) => request<Agent>("GET", `/agents/${enc(slug)}`, undefined, share),
  createAgent: (body: CreateAgentBody) => request<AgentCreated>("POST", "/agents", body),
  patchAgent: (
    slug: string,
    body: {
      name?: string;
      description?: string;
      auto_promote?: boolean;
      eval_repeats?: number;
      monthly_budget_usd?: number | null;
    },
  ) =>
    request<Agent>("PATCH", `/agents/${enc(slug)}`, body),
  createVersion: (slug: string, body: NewVersionBody) =>
    request<Version>("POST", `/agents/${enc(slug)}/versions`, body),
  getVersion: (slug: string, n: number | "prod") => request<Version>("GET", `/agents/${enc(slug)}/versions/${n}`),
  promote: (slug: string, version: number) => request<Agent>("POST", `/agents/${enc(slug)}/promote`, { version }),
  rotateKey: (slug: string) => request<{ api_key: string }>("POST", `/agents/${enc(slug)}/rotate-key`),
  templates: () => request<Template[]>("GET", "/templates"),
  draftAgent: (body: { description: string; examples?: string[]; constraints?: string }) =>
    request<AgentDraft>("POST", "/agent-drafts", body),
  putSecrets: (slug: string, values: Record<string, string>) =>
    request<{ names: string[] }>("PUT", `/agents/${enc(slug)}/secrets`, values),
  testTool: (slug: string, body: { tool: HttpTool; args: Record<string, unknown>; network?: boolean }) =>
    request<ToolTestResult>("POST", `/agents/${enc(slug)}/tools/test`, body),
  listMcpTools: (
    slug: string,
    body: { server: McpServer; network?: boolean; seed_files?: Record<string, string> },
  ) => request<{ tools: McpToolInfo[] }>("POST", `/agents/${enc(slug)}/mcp/list`, body),

  // runtime
  createRun: (
    slug: string,
    opts: { input: string; version?: number; files?: File[] },
    share?: string,
  ) => {
    if (opts.files && opts.files.length > 0) {
      const fd = new FormData();
      fd.append("input", opts.input);
      if (opts.version !== undefined) fd.append("version", String(opts.version));
      for (const f of opts.files) fd.append("files", f, f.name);
      return request<Run>("POST", `/agents/${enc(slug)}/runs`, fd, share);
    }
    const body: { input: string; version?: number } = { input: opts.input };
    if (opts.version !== undefined) body.version = opts.version;
    return request<Run>("POST", `/agents/${enc(slug)}/runs`, body, share);
  },
  listRuns: (slug: string, limit = 50, share?: string) =>
    request<Run[]>("GET", `/agents/${enc(slug)}/runs?limit=${limit}`, undefined, share),
  getRun: (id: string, share?: string) => request<Run>("GET", `/runs/${enc(id)}`, undefined, share),
  getEvents: (id: string, share?: string) => request<ForgeEvent[]>("GET", `/runs/${enc(id)}/events.json`, undefined, share),
  getDiff: (id: string, share?: string) => request<string>("GET", `/runs/${enc(id)}/diff`, undefined, share),
  artifactsUrl: (id: string, share?: string) => withShare(`/v1/runs/${enc(id)}/artifacts`, share),
  eventsUrl: (id: string, after?: number, share?: string) => {
    let url = `/v1/runs/${enc(id)}/events`;
    if (after !== undefined && after >= 0) url += `?after=${after}`;
    return withShare(url, share);
  },
  feedback: (id: string, rating: 1 | -1, comment: string, share?: string) =>
    request<Feedback>("POST", `/runs/${enc(id)}/feedback`, { rating, comment }, share),

  // governance
  listApprovals: (runId: string, share?: string) =>
    request<Approval[]>("GET", `/runs/${enc(runId)}/approvals`, undefined, share),
  resolveApproval: (runId: string, approvalId: string, decision: "approve" | "deny", note = "") =>
    request<Approval>("POST", `/runs/${enc(runId)}/approvals/${enc(approvalId)}`, { decision, note }),
  governance: (slug: string) => request<Governance>("GET", `/agents/${enc(slug)}/governance`),

  // improvement
  listEvals: (slug: string) => request<EvalCase[]>("GET", `/agents/${enc(slug)}/evals`),
  createEval: (slug: string, body: NewEvalBody) => request<EvalCase>("POST", `/agents/${enc(slug)}/evals`, body),
  deleteEval: (id: string) => request<{ ok: boolean }>("DELETE", `/evals/${enc(id)}`),
  saveAsEval: (runId: string, body: { name?: string; checker_type?: string; checker?: string; split?: Split }) =>
    request<EvalCase>("POST", `/runs/${enc(runId)}/save-as-eval`, body),
  runEvals: (slug: string, version?: number) =>
    request<{ version: number; results: unknown[] }>(
      "POST",
      `/agents/${enc(slug)}/evals/run`,
      version !== undefined ? { version } : {},
    ),
  evalResults: (slug: string, version: number) =>
    request<EvalResults>("GET", `/agents/${enc(slug)}/evals/results?version=${version}`),
  // observability
  searchRuns: (slug: string, query: string, share?: string) =>
    requestWithTotal<Run>(`/agents/${enc(slug)}/runs${query ? `?${query}` : ""}`, share),
  searchAllRuns: (query: string) => requestWithTotal<Run>(`/runs${query ? `?${query}` : ""}`),
  overview: (days: number, tzOffsetMin = 0) =>
    request<Overview>("GET", `/metrics/overview?days=${days}&tz_offset_min=${tzOffsetMin}`),
  agentMetrics: (slug: string, days: number, includeEval = false, tzOffsetMin = 0) =>
    request<AgentMetrics>(
      "GET",
      `/agents/${enc(slug)}/metrics?days=${days}&include_eval=${includeEval}&tz_offset_min=${tzOffsetMin}`,
    ),
  timeline: (id: string) => request<Timeline>("GET", `/runs/${enc(id)}/timeline`),

  improve: (slug: string) => request<Improvement>("POST", `/agents/${enc(slug)}/improve`, {}),
  prune: (slug: string) => request<Improvement>("POST", `/agents/${enc(slug)}/prune`, {}),
  getImprovement: (id: string) => request<Improvement>("GET", `/improvements/${enc(id)}`),
  listImprovements: (slug: string) => request<Improvement[]>("GET", `/agents/${enc(slug)}/improvements`),
};

// ---------------------------------------------------------------- helpers

export const TOOL_NAMES = ["bash", "read_file", "write_file", "edit_file"] as const;

export const MODEL_PRESETS = [
  "anthropic:claude-sonnet-5-5",
  "anthropic:claude-opus-5-5",
  "openai_compat:http://localhost:11434/v1|qwen3:8b",
];

export function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

export function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function fmtDuration(run: Pick<Run, "started_at" | "ended_at" | "status">): string {
  if (!run.started_at) return "";
  const end = run.ended_at || (run.status === "running" ? Date.now() / 1000 : run.started_at);
  const s = Math.max(0, end - run.started_at);
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function isTerminal(status: RunStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "timeout" || status === "budget_exceeded";
}

export function fmtUsd(x: number | null | undefined): string {
  const v = Number(x || 0);
  return v === 0 || v >= 0.01 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}
