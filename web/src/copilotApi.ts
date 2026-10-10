// Client for the Forge assistant (CONTRACT.md "Assistant"): sessions, SSE turns, confirmations.

import { ApiError, request } from "./api";

export interface CopilotContext {
  page?: string;
  agent_slug?: string;
  run_id?: string;
  version?: number | string;
}

export interface CopilotSession {
  id: string;
  title: string;
  context: CopilotContext;
  created_at: number;
  updated_at: number;
  running: boolean;
}

export interface CopilotLink {
  label: string;
  href: string;
}

export interface ConfirmItem {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
}

export interface ConfirmRequest {
  id: string;
  tool: string;
  args: unknown;
  summary: string;
  items: ConfirmItem[];
}

/** One stored or streamed step of the conversation. */
export type CopilotEvent =
  | { seq: number; type: "user"; payload: { text: string; context: CopilotContext } }
  | {
      seq: number;
      type: "assistant";
      payload: { text: string; kind: "progress" | "final"; tool_calls?: { id: string; name: string }[] };
    }
  | { seq: number; type: "tool_call"; payload: { id: string; tool: string; args: Record<string, unknown>; risk: string } }
  | {
      seq: number;
      type: "tool_result";
      payload: {
        id: string;
        tool: string;
        risk: string;
        ok: boolean;
        summary: string;
        output: string;
        links: CopilotLink[];
        refresh: string[];
      };
    }
  | { seq: number; type: "confirm_request"; payload: ConfirmRequest }
  | { seq: number; type: "confirm_resolved"; payload: { id: string; approved: boolean; by: string } }
  | { seq: number; type: "error"; payload: { message: string } }
  | {
      seq: number;
      type: "final";
      payload: {
        status: "done" | "stopped" | "limit" | "error";
        message: string;
        steps: number;
        tokens_in: number;
        tokens_out: number;
        cost_usd: number | null;
      };
    };

export interface CopilotSessionDetail extends CopilotSession {
  messages: CopilotEvent[];
  pending_confirm: ConfirmRequest | null;
}

const enc = encodeURIComponent;

/** Parses a text/event-stream body, calling `onEvent` for each `data:` JSON message until the stream ends. */
export async function readSse(res: Response, onEvent: (e: CopilotEvent) => void): Promise<void> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split(/\r?\n\r?\n/);
    buf = blocks.pop() ?? "";
    for (const block of blocks) {
      const data = block
        .split(/\r?\n/)
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      try {
        const obj = JSON.parse(data);
        if (obj && obj.type) onEvent(obj as CopilotEvent);
      } catch {
        /* keep-alive or partial data */
      }
    }
  }
}

async function streamed(url: string, init: RequestInit, onEvent: (e: CopilotEvent) => void): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, `Could not reach the Forge server (${(e as Error).message})`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  try {
    await readSse(res, onEvent);
  } catch (e) {
    if ((e as Error).name !== "AbortError") throw e;
  }
}

export const copilot = {
  createSession: (context: CopilotContext) => request<CopilotSession>("POST", "/copilot/sessions", { context }),
  listSessions: (limit = 10) => request<CopilotSession[]>("GET", `/copilot/sessions?limit=${limit}`),
  getSession: (id: string) => request<CopilotSessionDetail>("GET", `/copilot/sessions/${enc(id)}`),
  confirm: (id: string, confirmId: string, approve: boolean) =>
    request<{ ok: boolean }>("POST", `/copilot/sessions/${enc(id)}/confirm`, { confirm_id: confirmId, approve }),
  cancel: (id: string) => request<{ ok: boolean; cancelled: boolean }>("POST", `/copilot/sessions/${enc(id)}/cancel`),
  /** Sends a message; resolves when the turn's stream ends. */
  send: (id: string, text: string, context: CopilotContext, onEvent: (e: CopilotEvent) => void, signal?: AbortSignal) =>
    streamed(
      `/v1/copilot/sessions/${enc(id)}/messages`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, context }), signal },
      onEvent,
    ),
  /** Re-attaches to a running turn after a reload. */
  resume: (id: string, after: number, onEvent: (e: CopilotEvent) => void, signal?: AbortSignal) =>
    streamed(`/v1/copilot/sessions/${enc(id)}/events?after=${after}`, { signal }, onEvent),
};
