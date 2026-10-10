import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewAgentForm from "../../src/components/NewAgentForm";

const DRAFT = {
  name: "Issue triager",
  slug: "issue-triager",
  description: "Labels new issues.",
  system_prompt: "You are an issue triager. Work step by step.",
  tools: ["bash", "read_file"],
  network: true,
  network_reason: "Needs the GitHub REST API.",
  suggested_secrets: [{ name: "GITHUB_TOKEN", why: "Authenticates API requests." }],
  seed_files_hint: "None needed.",
  eval_cases: [
    { name: "crash-report", input: "Triage #12", checker_type: "judge", checker: "PASS only if labeled bug.", split: "train" },
    { name: "feature-request", input: "Triage #31", checker_type: "judge", checker: "PASS only if labeled feature.", split: "train" },
    { name: "mixed-batch", input: "Triage #40 and #41", checker_type: "shell", checker: "test -f triage.md", split: "holdout" },
  ],
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
let draftResponse: () => Response;

beforeEach(() => {
  draftResponse = () => json(DRAFT);
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/v1/templates") return json([]);
    if (url === "/v1/agent-drafts") return draftResponse();
    if (url === "/v1/agents" && init?.method === "POST") return json({ id: "ag_1", slug: "issue-triager", name: "n", api_key: "fk_1" });
    if (url === "/v1/agents/issue-triager/secrets" && init?.method === "PUT") return json({ names: ["GITHUB_TOKEN"] });
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const bodyOf = (url: string, method = "POST") => {
  const call = fetchMock.mock.calls.find(([u, i]) => u === url && i?.method === method);
  return call ? JSON.parse(call[1].body as string) : undefined;
};

async function draftIt() {
  const onCreated = vi.fn();
  render(<NewAgentForm onCreated={onCreated} onCancel={vi.fn()} />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Describe what the agent should do"), "Triage GitHub issues");
  await user.click(screen.getByRole("button", { name: "Add example task" }));
  await user.type(screen.getByLabelText("Example task 1"), "Triage #3");
  await user.type(screen.getByLabelText(/Constraints/), "Never close issues");
  await user.click(screen.getByRole("button", { name: "Draft agent" }));
  await screen.findByTestId("draft-notice");
  return { user, onCreated };
}

describe("NewAgentForm describe step", () => {
  it("starts on the describe step; Draft agent is disabled until there is a description", async () => {
    render(<NewAgentForm onCreated={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Draft agent" })).toBeDisabled();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start blank" }));
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.queryByTestId("starter-cases")).not.toBeInTheDocument();
  });

  it("shows the server error and stays on the describe step", async () => {
    draftResponse = () => json({ detail: "The model returned an invalid draft twice" }, 502);
    render(<NewAgentForm onCreated={vi.fn()} onCancel={vi.fn()} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Describe what the agent should do"), "x");
    await user.click(screen.getByRole("button", { name: "Draft agent" }));
    expect(await screen.findByText(/invalid draft twice/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Draft agent" })).toBeEnabled();
  });
});

describe("NewAgentForm draft review", () => {
  it("sends description, examples and constraints, then prefills editable fields", async () => {
    await draftIt();
    expect(bodyOf("/v1/agent-drafts")).toEqual({
      description: "Triage GitHub issues",
      examples: ["Triage #3"],
      constraints: "Never close issues",
    });
    expect(screen.getByLabelText("Name")).toHaveValue("Issue triager");
    expect(screen.getByLabelText("Slug")).toHaveValue("issue-triager");
    expect(screen.getByLabelText("Instructions")).toHaveValue(DRAFT.system_prompt);
    expect(screen.getByRole("checkbox", { name: "bash" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "write_file" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: /Network access/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("network-reason")).toHaveTextContent("Needs the GitHub REST API.");
    expect(within(screen.getByTestId("suggested-secrets")).getByText("GITHUB_TOKEN")).toBeInTheDocument();
    expect(screen.getAllByTestId("starter-case")).toHaveLength(3);
    expect(screen.getByText(/3 of 3 selected/)).toBeInTheDocument();
  });

  it("creates the agent with only the selected, edited cases and saves entered secrets", async () => {
    const { user, onCreated } = await draftIt();
    await user.click(screen.getByRole("checkbox", { name: "Include feature-request" }));
    expect(screen.getByText(/2 of 3 selected/)).toBeInTheDocument();
    const name = screen.getByLabelText("Case 1 name");
    await user.clear(name);
    await user.type(name, "crash-on-start");
    await user.selectOptions(screen.getByLabelText("Case 1 split"), "holdout");
    const prompt = screen.getByLabelText("Instructions");
    await user.type(prompt, " Be precise.");
    await user.type(screen.getByLabelText("Value for GITHUB_TOKEN"), "ghp_secret");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = bodyOf("/v1/agents");
    expect(body.system_prompt).toBe(DRAFT.system_prompt + " Be precise.");
    expect(body.network).toBe(true);
    expect(body.tools).toEqual(["bash", "read_file"]);
    expect(body.eval_cases).toEqual([
      { name: "crash-on-start", input: "Triage #12", checker_type: "judge", checker: "PASS only if labeled bug.", split: "holdout" },
      { name: "mixed-batch", input: "Triage #40 and #41", checker_type: "shell", checker: "test -f triage.md", split: "holdout" },
    ]);
    expect(bodyOf("/v1/agents/issue-triager/secrets", "PUT")).toEqual({ GITHUB_TOKEN: "ghp_secret" });
  });

  it("does not save secrets that were left empty, and Edit description goes back", async () => {
    const { user, onCreated } = await draftIt();
    await user.click(screen.getByRole("button", { name: /Edit description/ }));
    expect(screen.getByLabelText("Describe what the agent should do")).toHaveValue("Triage GitHub issues");
    await user.click(screen.getByRole("button", { name: "Draft agent" }));
    await screen.findByTestId("draft-notice");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(bodyOf("/v1/agents/issue-triager/secrets", "PUT")).toBeUndefined();
    expect(bodyOf("/v1/agents").eval_cases).toHaveLength(3);
  });
});
