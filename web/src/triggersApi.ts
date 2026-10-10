// Trigger endpoints (CONTRACT.md "Triggers").
import { request, type Run } from "./api";

export type TriggerKind = "schedule" | "github" | "slack";
export type GithubEvent = "issues" | "issue_comment" | "pull_request" | "push";

export interface Trigger {
  id: string;
  agent_id: string;
  kind: TriggerKind;
  config: Record<string, unknown> & {
    cron?: string;
    timezone?: string;
    input_template?: string;
    events?: GithubEvent[];
    label?: string;
    command?: string;
    branches?: string[];
    skip_if_running?: boolean;
    comment_back?: boolean;
    version?: number | null;
    max_runs_per_hour?: number;
  };
  enabled: boolean;
  created_at: number;
  last_fired_at: number | null;
  last_run_id: string | null;
  last_error: string | null;
  has_secret: boolean;
  has_token: boolean;
  hook_path: string | null;
  public_url: string | null;
  next_fire_times?: number[];
}

export interface TriggerCreated extends Trigger {
  secret?: string;
}

export interface TriggerFire {
  id: string;
  trigger_id: string;
  status: "started" | "skipped" | "missed" | "rate_limited" | "error";
  detail: string;
  test: boolean;
  run_id: string | null;
  delivery: string | null;
  delivery_error: string | null;
  created_at: number;
  run: Run | null;
}

export interface NewTrigger {
  kind: TriggerKind;
  config: Record<string, unknown>;
  enabled?: boolean;
  signing_secret?: string;
  token?: string;
}

const enc = encodeURIComponent;

export const triggersApi = {
  list: (slug: string) => request<Trigger[]>("GET", `/agents/${enc(slug)}/triggers`),
  create: (slug: string, body: NewTrigger) => request<TriggerCreated>("POST", `/agents/${enc(slug)}/triggers`, body),
  patch: (
    id: string,
    body: { config?: Record<string, unknown>; enabled?: boolean; signing_secret?: string; token?: string },
  ) => request<Trigger>("PATCH", `/triggers/${enc(id)}`, body),
  remove: (id: string) => request<{ ok: boolean }>("DELETE", `/triggers/${enc(id)}`),
  test: (id: string) => request<{ run_id: string; input: string }>("POST", `/triggers/${enc(id)}/test`, {}),
  history: (id: string, limit = 10) => request<TriggerFire[]>("GET", `/triggers/${enc(id)}/history?limit=${limit}`),
  rotateSecret: (id: string) => request<{ secret: string }>("POST", `/triggers/${enc(id)}/rotate-secret`),
};

/** Absolute webhook URL: FORGE_PUBLIC_URL when the server has one, else the page's own origin. */
export function hookUrl(t: Pick<Trigger, "hook_path" | "public_url">, origin: string): string {
  return `${(t.public_url || origin).replace(/\/+$/, "")}${t.hook_path ?? ""}`;
}
