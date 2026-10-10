// Typed client for the Forge HTTP API. Shapes mirror CONTRACT.md.

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "timeout";
export type Split = "train" | "holdout";
export type CreatedBy = "human" | "optimizer";
export type ImprovementStatus = "running" | "promoted" | "eligible" | "rejected" | "failed";
export type EventType = "run_started" | "assistant" | "tool_call" | "tool_result" | "run_finished" | "error";

export interface Version {
  id: string;
  agent_id: string;
  n: number;
  system_prompt: string;
  lessons: string[];
  model: string;
  tools: string[];
  http_tools: unknown[];
  network: boolean;
  seed_files: Record<string, string>;
  parent_id: string | null;
  created_by: CreatedBy;
  rationale: string;
  created_at: number;
  // Per-lesson provenance keyed by lesson text (see CONTRACT.md "Lesson provenance").
  lesson_meta?: Record<string, LessonMeta>;
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
  share_token: string;
  created_at: number;
  versions?: Version[];
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
  has_artifacts: boolean;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  feedback: Feedback[];
  eval_case_id?: string | null;
  // Not in CONTRACT.md; used if the backend includes it.
  agent_slug?: string;
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
  passed: boolean;
  reason: string;
  run_id: string | null;
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
  results: { case_id: string; passed: boolean; reason: string; run_id: string | null }[];
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
  rationale?: string;
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

const enc = encodeURIComponent;

// ---------------------------------------------------------------- endpoints

export const api = {
  // builder
  listAgents: () => request<Agent[]>("GET", "/agents"),
  getAgent: (slug: string, share?: string) => request<Agent>("GET", `/agents/${enc(slug)}`, undefined, share),
  createAgent: (body: CreateAgentBody) => request<AgentCreated>("POST", "/agents", body),
  patchAgent: (slug: string, body: { name?: string; description?: string; auto_promote?: boolean }) =>
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
  return status === "succeeded" || status === "failed" || status === "timeout";
}
