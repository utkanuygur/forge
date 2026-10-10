import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AgentsPage from "../../src/pages/AgentsPage";
import { ToastProvider } from "../../src/components/Toast";

const EXAMPLES = [
  {
    key: "log-triage",
    name: "Log triage",
    description: "Investigates production logs.",
    agent_slug: "log-triage",
    installed: false,
    eval_cases: 4,
    suggested_prompts: 3,
    needs_network: false,
  },
  {
    key: "repo-maintainer",
    name: "Repo maintainer",
    description: "Maintains a small repo.",
    agent_slug: "repo-maintainer",
    installed: true,
    eval_cases: 7,
    suggested_prompts: 4,
    needs_network: true,
  },
];

const AGENT = { id: "ag_1", slug: "log-triage", name: "Log triage", description: "", prod_version: 1, auto_promote: false, share_token: "t", created_at: 0 };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let agents: unknown[];
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  agents = [];
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/v1/agents" && method === "GET") return json(agents);
    if (url === "/v1/examples") return json(EXAMPLES);
    if (url === "/v1/examples/install-all") {
      agents = [AGENT];
      return json({ installed: [{ ...AGENT, api_key: "fk_1", example: "log-triage", created: true }], skipped: ["repo-maintainer"] });
    }
    if (url === "/v1/examples/log-triage/install") return json({ ...AGENT, api_key: "fk_1", example: "log-triage", created: true });
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function setup() {
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<AgentsPage />} />
          <Route path="/agents/:slug" element={<div>agent page</div>} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
  return userEvent.setup();
}

const bodyOf = (url: string) => {
  const call = fetchMock.mock.calls.find(([u, i]) => u === url && i?.method === "POST");
  return JSON.parse(call![1].body as string);
};

describe("Example agents on the Agents page", () => {
  it("shows the empty state card and Add all installs, refreshes the list and toasts", async () => {
    const user = setup();
    const card = await screen.findByTestId("examples-empty-card");
    expect(within(card).getByRole("heading", { name: "Add example agents" })).toBeInTheDocument();
    expect(card).toHaveTextContent("Four ready to run agents with test cases: repo maintainer, log triage, data analyst, docs writer");
    await user.click(within(card).getByRole("button", { name: "Add all" }));
    expect(bodyOf("/v1/examples/install-all")).toEqual({});
    expect(await screen.findByRole("status")).toHaveTextContent("Added 1 example agent");
    await waitFor(() => expect(screen.getAllByTestId("agent-card")).toHaveLength(1));
    expect(screen.queryByTestId("examples-empty-card")).not.toBeInTheDocument();
  });

  it("hides the card when agents exist but keeps the Examples header button", async () => {
    agents = [AGENT];
    setup();
    await screen.findAllByTestId("agent-card");
    expect(screen.queryByTestId("examples-empty-card")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Examples" })).toBeInTheDocument();
  });

  it("Choose opens the modal with counts, the Installed badge and a model select; Add navigates", async () => {
    const user = setup();
    const card = await screen.findByTestId("examples-empty-card");
    await user.click(within(card).getByRole("button", { name: "Choose…" }));
    const dialog = await screen.findByRole("dialog", { name: "Example agents" });
    const rows = await within(dialog).findAllByTestId("example-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("4 test cases, 3 starter prompts");
    expect(within(rows[0]).queryByText("Installed")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("Installed")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "Open Repo maintainer" })).toHaveAttribute("href", "/agents/repo-maintainer");
    expect(within(dialog).getByRole("button", { name: "Add all (1)" })).toBeEnabled();

    const select = within(dialog).getByLabelText("Model");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(
      expect.arrayContaining(["Server default", "Claude Sonnet 5.5", "Local Qwen via Ollama (qwen3:4b-instruct)"]),
    );
    await user.selectOptions(select, "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct");
    expect(within(dialog).getByText(/ollama pull qwen3:4b-instruct/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Add Log triage" }));
    expect(bodyOf("/v1/examples/log-triage/install")).toEqual({ model: "openai_compat:http://localhost:11434/v1|qwen3:4b-instruct" });
    expect(await screen.findByText("agent page")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Added Log triage");
  });

  it("shows server errors in the modal", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/v1/agents") return json([]);
      if (url === "/v1/examples") return json(EXAMPLES);
      if (init?.method === "POST") return json({ detail: "An agent with the slug 'log-triage' already exists." }, 409);
      return json({}, 500);
    });
    const user = setup();
    await user.click(await screen.findByRole("button", { name: "Examples" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("button", { name: "Add Log triage" }));
    expect(await within(dialog).findByText(/already exists/)).toBeInTheDocument();
  });
});
