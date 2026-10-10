import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SuggestedPrompts from "../../src/components/SuggestedPrompts";
import RunForm from "../../src/components/RunForm";

const PROMPTS = [
  { title: "Find the 5xx cause", prompt: "Investigate the logs and write answer.json" },
  { title: "Slow queries", prompt: "Find the slowest database query window" },
];

describe("SuggestedPrompts", () => {
  it("renders nothing without prompts", () => {
    const { container } = render(<SuggestedPrompts prompts={[]} onPick={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    render(<SuggestedPrompts onPick={() => {}} />);
    expect(screen.queryByTestId("suggested-prompts")).toBeNull();
  });

  it("shows one chip per prompt with the full prompt as a tooltip and reports clicks", async () => {
    const onPick = vi.fn();
    render(<SuggestedPrompts prompts={PROMPTS} onPick={onPick} selected={PROMPTS[1].prompt} />);
    const chips = screen.getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["Find the 5xx cause", "Slow queries"]);
    expect(chips[0]).toHaveAttribute("title", PROMPTS[0].prompt);
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(chips[1]).toHaveAttribute("aria-pressed", "true");
    await userEvent.setup().click(chips[0]);
    expect(onPick).toHaveBeenCalledWith(PROMPTS[0]);
  });
});

describe("RunForm starter chips", () => {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => json({ id: "r_1", status: "queued" }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("fills the task with the picked prompt, which then runs as is", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<RunForm slug="log-triage" suggestions={PROMPTS} onCreated={onCreated} />);
    const task = screen.getByLabelText("Task") as HTMLTextAreaElement;
    expect(task.value).toBe("");
    await user.click(screen.getByRole("button", { name: "Slow queries" }));
    expect(task.value).toBe(PROMPTS[1].prompt);
    expect(task).toHaveFocus();
    expect(screen.getByRole("button", { name: "Slow queries" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: /^Run$/ }));
    expect(onCreated).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/agents/log-triage/runs");
    const body = init.body instanceof FormData ? init.body.get("input") : JSON.parse(String(init.body)).input;
    expect(body).toBe(PROMPTS[1].prompt);
  });

  it("shows no chips when the agent has no suggestions", () => {
    render(<RunForm slug="x" onCreated={() => {}} />);
    expect(screen.queryByTestId("suggested-prompts")).toBeNull();
  });
});
