import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareChat, { friendlyError, LIMIT_MESSAGES } from "../../src/components/share/ShareChat";
import { ApiError, setShareVisitor, type ForgeEvent, type Run, type Session } from "../../src/api";

const TOKEN = "tok";
const AGENT = {
  slug: "helper",
  name: "Helper",
  description: "",
  suggested_prompts: [{ title: "Write a poem", prompt: "Write a short poem in poem.md" }],
};

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function run(id: string, i: number, input: string, extra: Partial<Run> = {}): Run {
  return {
    id,
    agent_id: "ag_1",
    version_id: "v_1",
    version_n: 1,
    source: "share",
    input,
    status: "succeeded",
    output: `Answer ${i + 1}`,
    error: null,
    turns: 2,
    tokens_in: 10,
    tokens_out: 5,
    has_artifacts: true,
    created_at: 1,
    started_at: 1,
    ended_at: 2,
    feedback: [],
    session_id: "s_1",
    turn_index: i,
    ...extra,
  } as Run;
}

function events(runId: string, i: number): ForgeEvent[] {
  const e = (seq: number, type: ForgeEvent["type"], payload: Record<string, unknown>): ForgeEvent => ({
    run_id: runId,
    seq,
    ts: 1,
    type,
    payload,
  });
  return [
    e(0, "run_started", {}),
    e(1, "tool_call", { id: "c1", name: "write_file", args: { path: "poem.md", content: "roses" } }),
    e(2, "tool_result", { id: "c1", output: "wrote poem.md", is_error: false }),
    e(3, "assistant", { text: `Answer ${i + 1}`, kind: "final" }),
    e(4, "run_finished", { status: "succeeded", output: `Answer ${i + 1}`, turns: 2 }),
  ];
}

class FakeEventSource {
  url: string;
  onmessage: ((m: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((m: MessageEvent) => void)[]>();
  constructor(url: string) {
    this.url = url;
    const id = /\/runs\/([^/]+)\/events/.exec(url)?.[1] ?? "";
    setTimeout(() => {
      const i = state.turns.findIndex((t) => t.id === id);
      for (const ev of i >= 0 ? events(id, i) : []) this.emit(ev.type, ev);
      this.emit("end", {});
    }, 0);
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), fn]);
  }
  close() {}
  emit(type: string, data: unknown) {
    const m = { data: JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners.get(type) || []) fn(m);
  }
}

interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

const state = {
  turns: [] as Run[],
  sendStatus: 200,
  sendBody: null as unknown,
  calls: [] as Call[],
};

function session(): Session {
  return {
    id: "s_1",
    agent_slug: "helper",
    version_n: 1,
    draft: null,
    title: state.turns[0]?.input ?? "",
    created_at: 1,
    updated_at: 2,
    turns: state.turns,
    turn_count: state.turns.length,
    channel: "share",
    expired: false,
    max_turns: 20,
  };
}

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { value: memoryStorage(), configurable: true });
  setShareVisitor(TOKEN, null);
  state.turns = [];
  state.sendStatus = 200;
  state.sendBody = null;
  state.calls = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const u = String(url);
      const method = init.method || "GET";
      state.calls.push({
        method,
        url: u,
        headers: (init.headers || {}) as Record<string, string>,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (method === "POST" && u.startsWith("/v1/agents/helper/sessions"))
        return json({ ...session(), visitor_id: "vis_abc" }, 200, { "x-forge-visitor": "vis_abc" });
      if (method === "GET" && u.startsWith("/v1/agents/helper/sessions")) return json(state.turns.length ? [session()] : []);
      if (method === "GET" && u.startsWith("/v1/sessions/s_1")) return json(session());
      if (method === "POST" && u.startsWith("/v1/sessions/s_1/messages")) {
        if (state.sendStatus !== 200) return json(state.sendBody, state.sendStatus, { "retry-after": "3600" });
        const input = (JSON.parse(String(init.body)) as { input: string }).input;
        const r = run(`r_${state.turns.length + 1}`, state.turns.length, input);
        state.turns = [...state.turns, r];
        return json(r);
      }
      if (method === "POST" && /\/v1\/runs\/[^/]+\/feedback/.test(u))
        return json({ id: "fb_1", run_id: "r_1", rating: 1, comment: "", source: "share", created_at: 1 });
      if (u.startsWith("/v1/runs/") && u.includes("/files"))
        return json([{ path: "poem.md", size: 5, is_text: true, changed: true, change: "added" }]);
      return json({ detail: `unexpected ${method} ${u}` }, 500);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ShareChat", () => {
  it("shows starter prompts and no configuration in the empty state", () => {
    render(<ShareChat agent={AGENT} share={TOKEN} />);
    expect(screen.getByText("Write a poem")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveAttribute(
      "placeholder",
      "What would you like the agent to do?",
    );
    expect(screen.queryByText(/system prompt|version/i)).not.toBeInTheDocument();
  });

  it("starts a conversation on Enter, stores the visitor and sends it with later requests", async () => {
    const onChange = vi.fn();
    render(<ShareChat agent={AGENT} share={TOKEN} onSessionChange={onChange} />);
    const box = screen.getByRole("textbox", { name: "Message" });
    await userEvent.type(box, "first line{Shift>}{Enter}{/Shift}second line");
    expect(box).toHaveValue("first line\nsecond line");
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Answer 1");
    const create = state.calls.find((c) => c.method === "POST" && c.url.startsWith("/v1/agents/helper/sessions"))!;
    expect(create.headers["X-Share-Token"]).toBe(TOKEN);
    expect(create.headers["X-Forge-Visitor"]).toBeUndefined();
    const send = state.calls.find((c) => c.url.startsWith("/v1/sessions/s_1/messages"))!;
    expect(send.headers["X-Forge-Visitor"]).toBe("vis_abc");
    expect(send.body).toEqual({ input: "first line\nsecond line" });
    expect(window.localStorage.getItem("forge.share.visitor.helper")).toBe("vis_abc");
    expect(onChange).toHaveBeenCalledWith("s_1");
  });

  it("collapses the steps, shows the final answer, and posts thumbs with the share token", async () => {
    state.turns = [run("r_1", 0, "write a poem")];
    window.localStorage.setItem("forge.share.visitor.helper", "vis_abc");
    render(<ShareChat agent={AGENT} share={TOKEN} initialSession="s_1" />);
    const answer = await screen.findByTestId("final-answer");
    expect(answer).toHaveTextContent("Answer 1");
    const steps = await screen.findByTestId("steps");
    expect(steps).not.toHaveAttribute("open");
    expect(within(steps).getByText("Show 1 step")).toBeInTheDocument();
    expect(screen.getByTestId("user-message")).toHaveTextContent("write a poem");
    await userEvent.click(screen.getByRole("button", { name: "Helpful" }));
    await screen.findByText("Thanks for the feedback");
    const fb = state.calls.find((c) => c.url.includes("/feedback"))!;
    expect(fb.headers["X-Forge-Visitor"]).toBe("vis_abc");
    expect(fb.url).toContain("share=tok");
  });

  it("opens the session workspace in the files panel with a download link", async () => {
    state.turns = [run("r_1", 0, "write a poem")];
    window.localStorage.setItem("forge.share.visitor.helper", "vis_abc");
    render(<ShareChat agent={AGENT} share={TOKEN} initialSession="s_1" />);
    await screen.findByTestId("final-answer");
    await userEvent.click(screen.getByRole("button", { name: "Files" }));
    expect((await screen.findAllByText("poem.md")).length).toBeGreaterThan(0);
    expect(screen.getByTestId("download-workspace")).toHaveAttribute(
      "href",
      "/v1/runs/r_1/artifacts?share=tok&visitor=vis_abc",
    );
  });

  it("lists the visitor's recent conversations", async () => {
    state.turns = [run("r_1", 0, "write a poem")];
    window.localStorage.setItem("forge.share.visitor.helper", "vis_abc");
    render(<ShareChat agent={AGENT} share={TOKEN} />);
    await userEvent.click(await screen.findByRole("button", { name: "Recent conversations" }));
    const list = screen.getByTestId("recent-conversations");
    expect(within(list).getByText("write a poem")).toBeInTheDocument();
  });

  it("shows the daily limit inline", async () => {
    state.turns = [run("r_1", 0, "write a poem")];
    state.sendStatus = 429;
    state.sendBody = { detail: "server text", limit: "daily_turns", retry_after: 3600 };
    window.localStorage.setItem("forge.share.visitor.helper", "vis_abc");
    render(<ShareChat agent={AGENT} share={TOKEN} initialSession="s_1" />);
    await screen.findByTestId("final-answer");
    const box = screen.getByRole("textbox", { name: "Message" });
    await userEvent.type(box, "again{Enter}");
    await waitFor(() =>
      expect(screen.getByTestId("share-notice")).toHaveTextContent(
        "You've reached today's limit for this link. Try again tomorrow.",
      ),
    );
  });

  it("maps every limit and budget error to plain copy", () => {
    expect(friendlyError(new ApiError(429, "x", "session_turns"))).toBe(LIMIT_MESSAGES.session_turns);
    expect(friendlyError(new ApiError(429, "x", "concurrent_turns"))).toMatch(/busy/);
    expect(friendlyError(new ApiError(402, "Monthly budget of $5"))).not.toMatch(/\$/);
    expect(friendlyError(new ApiError(410, "x"))).toMatch(/expired/);
  });
});
