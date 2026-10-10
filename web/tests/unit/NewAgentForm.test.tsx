import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NewAgentForm from "../../src/components/NewAgentForm";

const TEMPLATES = [
  {
    key: "repo",
    name: "Repo Maintainer",
    description: "Keeps a repo healthy",
    system_prompt: "You maintain repositories.",
    tools: ["bash", "read_file"],
    network: true,
  },
];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
let createResponse: () => Response;

beforeEach(() => {
  createResponse = () => json({ id: "ag_1", slug: "s", name: "n", api_key: "fk_123" });
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/v1/templates") return json(TEMPLATES);
    if (url === "/v1/agents" && init?.method === "POST") return createResponse();
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

async function setup({ blank = true } = {}) {
  const onCreated = vi.fn();
  const onCancel = vi.fn();
  render(<NewAgentForm onCreated={onCreated} onCancel={onCancel} />);
  const user = userEvent.setup();
  if (blank) await user.click(screen.getByRole("button", { name: "Start blank" }));
  return { onCreated, onCancel, user };
}

const createBody = () => {
  const call = fetchMock.mock.calls.find(([u, i]) => u === "/v1/agents" && i?.method === "POST");
  return JSON.parse(call![1].body as string);
};

describe("NewAgentForm slug generation", () => {
  it("derives the slug from the name while the slug is untouched", async () => {
    const { user } = await setup();
    await user.type(screen.getByLabelText("Name"), "My Cool Agent!");
    expect(screen.getByLabelText("Slug")).toHaveValue("my-cool-agent");
  });

  it("stops deriving once the slug is edited by hand", async () => {
    const { user } = await setup();
    await user.type(screen.getByLabelText("Name"), "First");
    const slug = screen.getByLabelText("Slug");
    await user.clear(slug);
    await user.type(slug, "custom-slug");
    await user.type(screen.getByLabelText("Name"), " Second");
    expect(slug).toHaveValue("custom-slug");
  });

  it("template prefill sets name, slug, description, instructions, tools and network", async () => {
    const { user } = await setup();
    await user.click(await screen.findByRole("button", { name: "Repo Maintainer" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Repo Maintainer");
    expect(screen.getByLabelText("Slug")).toHaveValue("repo-maintainer");
    expect(screen.getByLabelText("Description")).toHaveValue("Keeps a repo healthy");
    expect(screen.getByLabelText("Instructions")).toHaveValue("You maintain repositories.");
    expect(screen.getByRole("checkbox", { name: "bash" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "write_file" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: /Network access/ })).toHaveAttribute("aria-checked", "true");
  });

  it("template prefill keeps a hand edited slug", async () => {
    const { user } = await setup();
    await user.type(screen.getByLabelText("Slug"), "mine");
    await user.click(await screen.findByRole("button", { name: "Repo Maintainer" }));
    expect(screen.getByLabelText("Slug")).toHaveValue("mine");
  });
});

describe("NewAgentForm submit", () => {
  it("posts the form and omits an empty model", async () => {
    const { user, onCreated } = await setup();
    await user.type(screen.getByLabelText("Name"), "Hello Bot");
    await user.type(screen.getByLabelText("Instructions"), "Say hi");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    const body = createBody();
    expect(body).toMatchObject({
      name: "Hello Bot",
      slug: "hello-bot",
      system_prompt: "Say hi",
      tools: ["bash", "read_file", "write_file", "edit_file"],
      network: false,
      auto_promote: false,
    });
    expect(body).not.toHaveProperty("model");
    expect(onCreated.mock.calls[0][0].api_key).toBe("fk_123");
  });

  it("sends a trimmed model when set", async () => {
    const { user, onCreated } = await setup();
    await user.type(screen.getByLabelText("Name"), "M");
    await user.type(screen.getByLabelText("Instructions"), "x");
    await user.type(screen.getByLabelText("Model"), "  fake:hello  ");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(createBody().model).toBe("fake:hello");
  });

  it("shows the server error and re-enables the button", async () => {
    createResponse = () => json({ detail: "slug already exists" }, 409);
    const { user, onCreated } = await setup();
    await user.type(screen.getByLabelText("Name"), "Dup");
    await user.type(screen.getByLabelText("Instructions"), "x");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("slug already exists")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create agent" })).toBeEnabled();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("cancel calls onCancel", async () => {
    const { user, onCancel } = await setup();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
