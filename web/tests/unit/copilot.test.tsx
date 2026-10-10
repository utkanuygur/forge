import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Layout from "../../src/components/Layout";
import Conversation, { ConfirmCard, toItems, toolLabel } from "../../src/components/copilot/Conversation";
import { pageInfo } from "../../src/components/copilot/pageContext";
import { clampWidth } from "../../src/components/copilot/usePanel";
import { readSse, type CopilotEvent } from "../../src/copilotApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function renderApp(path = "/agents/repo-maintainer/evals") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/agents/:slug/:tab" element={<div>page</div>} />
          <Route path="/" element={<div>home</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
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
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const u = String(url);
    if (u.startsWith("/v1/agents/repo-maintainer")) return json({ slug: "repo-maintainer", name: "Repo maintainer" });
    if (u.startsWith("/v1/copilot/sessions?")) return json([]);
    return json({}, 404);
  });
});

describe("assistant panel", () => {
  it("opens from the nav button, toggles with Cmd+J and Ctrl+J, and remembers the state", async () => {
    renderApp();
    const panel = screen.getByTestId("assistant-panel");
    expect(panel).not.toBeVisible();
    fireEvent.click(screen.getByTestId("assistant-toggle"));
    expect(panel).toBeVisible();
    expect(window.localStorage.getItem("forge.assistant.open")).toBe("1");
    act(() => {
      fireEvent.keyDown(window, { key: "j", metaKey: true });
    });
    expect(panel).not.toBeVisible();
    act(() => {
      fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    });
    expect(panel).toBeVisible();
    expect(screen.getByTestId("assistant-toggle")).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the page context in the header and page specific starters", async () => {
    window.localStorage.setItem("forge.assistant.open", "1");
    renderApp();
    await waitFor(() => expect(screen.getByTestId("assistant-context")).toHaveTextContent("Repo maintainer · Evaluations"));
    expect(screen.getByTestId("assistant-empty")).toHaveTextContent("ask before changing anything");
    for (const label of ["Add eval cases", "Why are these failing?", "Run evaluation"])
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });

  it("clamps the width between 360 and 520", () => {
    expect(clampWidth(100)).toBe(360);
    expect(clampWidth(900)).toBe(520);
    expect(clampWidth(Number.NaN)).toBe(400);
  });
});

describe("page context", () => {
  it("maps routes to context and labels", () => {
    expect(pageInfo("/agents/a/runs").context).toEqual({ page: "/agents/a/runs", agent_slug: "a" });
    expect(pageInfo("/agents/a").place).toBe("Configure");
    expect(pageInfo("/runs/r_1").context.run_id).toBe("r_1");
    expect(pageInfo("/dashboard").place).toBe("Dashboard");
  });
});

const EVENTS: CopilotEvent[] = [
  { seq: 0, type: "user", payload: { text: "how many cases?", context: {} } },
  { seq: 1, type: "assistant", payload: { text: "", kind: "progress", tool_calls: [{ id: "c1", name: "list_eval_cases" }] } },
  { seq: 2, type: "tool_call", payload: { id: "c1", tool: "list_eval_cases", args: { slug: "repo-maintainer" }, risk: "read" } },
  {
    seq: 3,
    type: "tool_result",
    payload: {
      id: "c1", tool: "list_eval_cases", risk: "read", ok: true, summary: "7 cases", output: "...",
      links: [{ label: "Evaluations", href: "/agents/repo-maintainer/evals" }], refresh: [],
    },
  },
  { seq: 4, type: "assistant", payload: { text: "It has **7** cases. See [the tab](/agents/repo-maintainer/evals).", kind: "final", tool_calls: [] } },
];

describe("conversation", () => {
  it("renders compact tool steps like the transcript and navigates in the app", () => {
    const nav = vi.fn();
    render(<Conversation items={toItems(EVENTS)} onNavigate={nav} onAnswer={() => {}} />);
    const step = screen.getByTestId("tool-step");
    expect(step).toHaveTextContent("● list_eval_cases(repo-maintainer)");
    expect(step).toHaveTextContent("⎿7 cases");
    expect(screen.getByTestId("user-bubble")).toHaveTextContent("how many cases?");
    fireEvent.click(screen.getByRole("link", { name: "the tab" }));
    expect(nav).toHaveBeenCalledWith("/agents/repo-maintainer/evals");
    fireEvent.click(screen.getByRole("link", { name: "Evaluations →" }));
    expect(nav).toHaveBeenCalledTimes(2);
  });

  it("labels tool calls with their key arguments", () => {
    expect(toolLabel("create_eval_case", { slug: "s", name: "refund", input: "x" })).toBe("create_eval_case(s, refund)");
    expect(toolLabel("list_agents", {})).toBe("list_agents()");
  });

  it("confirm card lists every change and answers", () => {
    const onAnswer = vi.fn();
    const req = {
      id: "cf_1", tool: "create_eval_case", args: [], summary: "Add 2 eval cases to s: a, b. Free until evaluated.",
      items: [
        { call_id: "1", tool: "create_eval_case", args: { name: "a" }, summary: "Add train eval case \"a\" to s" },
        { call_id: "2", tool: "create_eval_case", args: { name: "b" }, summary: "Add train eval case \"b\" to s" },
      ],
    };
    const { rerender } = render(<ConfirmCard item={{ kind: "confirm", key: "k", req }} onAnswer={onAnswer} />);
    expect(screen.getByTestId("confirm-card")).toHaveTextContent("Add 2 eval cases to s");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onAnswer).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onAnswer).toHaveBeenCalledWith(false);
    rerender(
      <ConfirmCard item={{ kind: "confirm", key: "k", req, resolved: { approved: false, by: "user" } }} onAnswer={onAnswer} />,
    );
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByTestId("confirm-card")).toHaveTextContent("Denied");
  });
});

describe("readSse", () => {
  it("parses messages split across chunks", async () => {
    const enc = new TextEncoder();
    const parts = ['event: user\r\nid: 0\r\ndata: {"seq": 0, "type": "user", "pay', 'load": {"text": "hi"}}\r\n\r\n',
      ': ping\r\n\r\nevent: end\r\ndata: {}\r\n\r\n'];
    const body = new ReadableStream({
      start(c) {
        for (const p of parts) c.enqueue(enc.encode(p));
        c.close();
      },
    });
    const got: CopilotEvent[] = [];
    await readSse(new Response(body), (e) => got.push(e));
    expect(got).toEqual([{ seq: 0, type: "user", payload: { text: "hi" } }]);
  });
});
