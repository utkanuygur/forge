// Settings client (CONTRACT.md "Settings"): typed calls for /v1/settings and /v1/models/available, plus the
// optional desktop bridge the Forge macOS app injects into the main window.
import { ApiError, request } from "./api";

export type SettingSource = "env" | "saved" | "default";
export type SettingType = "string" | "int" | "float" | "bool" | "enum" | "model" | "url" | "json" | "secret";

export interface SettingView {
  key: string;
  env: string;
  type: SettingType;
  label: string;
  description: string;
  group: string;
  apply: "live" | "restart";
  secret: boolean;
  source: SettingSource;
  editable: boolean;
  needs_restart: boolean;
  error: string | null;
  value?: unknown;
  default?: unknown;
  options?: string[];
  min?: number;
  max?: number;
  widget?: "endpoints" | "prices";
  optional?: boolean;
  placeholder?: string;
  /** secrets only */
  is_set?: boolean;
  from_desktop?: boolean;
}

export interface SettingsGroup {
  name: string;
  settings: SettingView[];
}

export interface DockerStatus {
  status: "ok" | "error" | "skipped";
  detail?: string;
  server_version?: string;
  image?: string;
}

export interface SettingsPayload {
  desktop: boolean;
  needs_restart: boolean;
  groups: SettingsGroup[];
  info: { data_dir: string; version: string; executor: string; docker: DockerStatus };
}

export interface ProviderStatus {
  id: string;
  name: string;
  kind: "anthropic" | "openai_compat";
  base_url: string;
  /** Prefix that turns a model id into a Forge model string, e.g. "anthropic:" */
  prefix: string;
  configured: boolean;
  reachable: boolean;
  models: string[];
  error: string | null;
  checked_at: number;
  has_key?: boolean;
}

export interface Endpoint {
  name: string;
  base_url: string;
  api_key_env?: string;
}

/** Per key validation errors from a 422 PATCH, or null for any other error. */
export function settingErrors(e: unknown): Record<string, string> | null {
  if (e instanceof ApiError && e.status === 422) {
    const d = e.detail as { errors?: Record<string, string> } | undefined;
    if (d && d.errors) return d.errors;
  }
  return null;
}

export const settingsApi = {
  get: () => request<SettingsPayload>("GET", "/settings"),
  patch: (values: Record<string, unknown>) => request<SettingsPayload>("PATCH", "/settings", values),
  reset: (keys: string[]) => request<SettingsPayload>("POST", "/settings/reset", { keys }),
  models: (opts: { refresh?: boolean; provider?: string } = {}) => {
    const q = new URLSearchParams();
    if (opts.refresh) q.set("refresh", "true");
    if (opts.provider) q.set("provider", opts.provider);
    const qs = q.toString();
    return request<{ providers: ProviderStatus[] }>("GET", `/models/available${qs ? `?${qs}` : ""}`);
  },
};

// ---------------------------------------------------------------- desktop bridge

export interface DesktopBridge {
  /** Opens the native Settings window where API keys are stored in the Keychain. */
  openKeySettings: () => void;
  /** Restarts the Forge server; the window reloads on this page when it is back. */
  restartServer: () => Promise<{ ok: boolean; error?: string }>;
}

declare global {
  interface Window {
    forgeDesktop?: DesktopBridge;
  }
}

export function desktopBridge(): DesktopBridge | null {
  return typeof window !== "undefined" && window.forgeDesktop ? window.forgeDesktop : null;
}

// ---------------------------------------------------------------- helpers

export function flatSettings(p: SettingsPayload | null | undefined): Record<string, SettingView> {
  const out: Record<string, SettingView> = {};
  for (const g of p?.groups ?? []) for (const s of g.settings) out[s.key] = s;
  return out;
}

let publicUrl: Promise<string> | null = null;

/** The public_url setting (cached for the page's lifetime), or "" when unset or unavailable. */
export function getPublicUrl(): Promise<string> {
  publicUrl ??= settingsApi
    .get()
    .then((p) => String(flatSettings(p).public_url?.value || ""))
    .catch(() => "");
  return publicUrl;
}

export function resetPublicUrlCache() {
  publicUrl = null;
}
