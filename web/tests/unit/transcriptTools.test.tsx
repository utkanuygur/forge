import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import Transcript from "../../src/components/transcript/Transcript";
import { summarize } from "../../src/components/transcript/AgentTool";
import { toolKind, toolLabel } from "../../src/components/transcript/model";
import { api, type ForgeEvent } from "../../src/api";

let seq = 0;
const ev = (type: ForgeEvent["type"], payload: Record<string, unknown>, run = "r_parent"): ForgeEvent => ({
  run_id: run,
  seq: seq++,
  ts: 100,
  type,
  payload,
});

/** A delegation to `agent__<slug>` whose result points at `child`. */
function delegation(slug: string, child: string, run: string, output = "**Fixed** the bug.\nMore detail.") {
  const id = `call_${seq}`;
  return [
    ev("tool_call", { id, name: `agent__${slug}`, args: { input: `Please fix it\nwith care` } }, run),
    ev("tool_result", {
      id, name: `agent__${slug}`, output, is_error: false,
      child_run_id: child, child_agent: slug, child_status: "succeeded",
    }, run),
  ];
}

const show = (events: ForgeEvent[]) =>
  render(
    <MemoryRouter>
      <Transcript live={false} events={events} />
    </MemoryRouter>,
  );

afterEach(() => {
  vi.restoreAllMocks();
  seq = 0;
});

describe("tool kinds and labels", () => {
  it("recognizes agent, mcp and http tools and tolerates missing fields", () => {
    expect(toolKind("agent__fixer")).toBe("agent");
    expect(toolKind("mcp__github__list_issues")).toBe("mcp");
    expect(toolKind("lookup", { source: "http" })).toBe("http");
    expect(toolKind("lookup")).toBe("other");
    expect(toolLabel("agent__fixer", { input: "x" })).toEqual({ tool: "Agent", arg: "fixer" });
    expect(toolLabel("mcp__github__list_issues", { repo: "a/b" })).toEqual({
      tool: "github.list_issues",
      arg: '{"repo":"a/b"}',
    });
    expect(toolLabel("get_weather", { city: "SF" }, { source: "http", method: "get", url: "https://api.x/w" })).toEqual({
      tool: "get_weather",
      arg: "GET https://api.x/w",
    });
    expect(toolLabel("get_weather", { city: "SF" }, { source: "http" }).arg).toBe('{"city":"SF"}');
    expect(summarize("\n## **Done**: all `good`\nnext")).toBe("Done: all good");
  });
});

describe("delegated agent renderer", () => {
  it("shows Agent(slug), the delegated input, status summary, link, and a lazy nested transcript", async () => {
    const child: ForgeEvent[] = [
      ev("assistant", { text: "Looking at the repo.", kind: "progress" }, "r_child"),
      ev("tool_call", { id: "k1", name: "bash", args: { cmd: "make test" } }, "r_child"),
      ev("tool_result", { id: "k1", name: "bash", output: "exit_code: 0\n--- stdout ---\nok\n--- stderr ---\n" }, "r_child"),
      ev("assistant", { text: "Fixed.", kind: "final" }, "r_child"),
    ];
    const spy = vi.spyOn(api, "getEvents").mockResolvedValue(child);
    show(delegation("fixer", "r_child", "r_parent"));
    const card = document.querySelector('[data-tool="agent__fixer"]') as HTMLElement;
    expect(card).toHaveTextContent("● Agent(fixer)");
    expect(within(card).getByTestId("agent-input")).toHaveTextContent("Please fix it");
    expect(within(card).getByTestId("agent-input")).not.toHaveTextContent("with care");
    expect(within(card).getByTestId("agent-summary")).toHaveTextContent("succeeded: Fixed the bug.");
    expect(within(card).getByTestId("child-run-link")).toHaveAttribute("href", "/runs/r_child");
    expect(spy).not.toHaveBeenCalled(); // collapsed by default, nothing fetched
    await userEvent.click(within(card).getByTestId("agent-expand"));
    const nested = await within(card).findByTestId("nested-run");
    expect(spy).toHaveBeenCalledWith("r_child");
    expect(nested).toHaveAttribute("data-depth", "1");
    expect(nested).toHaveTextContent("● Bash(make test)");
    expect(nested).toHaveTextContent("Fixed.");
  });

  it("stops offering nested views after 3 levels", async () => {
    vi.spyOn(api, "getEvents").mockImplementation(async (id: string) => {
      const depth = Number(id.split("_")[1]);
      return delegation("deeper", `r_${depth + 1}`, id);
    });
    show(delegation("deeper", "r_1", "r_0"));
    for (let level = 1; level <= 3; level++) {
      const buttons = screen.getAllByTestId("agent-expand");
      await userEvent.click(buttons[buttons.length - 1]);
      await screen.findAllByTestId("nested-run").then((n) => expect(n).toHaveLength(level));
    }
    // Level 3 renders its delegation but only as a link.
    expect(screen.getAllByTestId("agent-expand")).toHaveLength(3);
    expect(screen.getAllByTestId("child-run-link")).toHaveLength(4);
  });

  it("failed child shows its status in red text", () => {
    show([
      ev("tool_call", { id: "x", name: "agent__fixer", args: { input: "go" } }),
      ev("tool_result", { id: "x", name: "agent__fixer", output: "boom", is_error: true, child_status: "failed" }),
    ]);
    const card = document.querySelector('[data-tool="agent__fixer"]') as HTMLElement;
    expect(card.dataset.error).toBe("true");
    expect(within(card).getByTestId("agent-summary")).toHaveTextContent("failed: boom");
    expect(within(card).queryByTestId("agent-expand")).toBeNull(); // no child run id: nothing to open
  });
});

describe("mcp and http renderers", () => {
  it("mcp tools render as server.tool(args) with raw output", () => {
    show([
      ev("tool_call", { id: "m", name: "mcp__github__list_issues", args: { repo: "a/b" } }),
      ev("tool_result", { id: "m", name: "mcp__github__list_issues", output: "#1 bug", is_error: false, source: "mcp" }),
    ]);
    const card = document.querySelector('[data-tool="mcp__github__list_issues"]') as HTMLElement;
    expect(card).toHaveTextContent('● github.list_issues({"repo":"a/b"})');
    expect(card).toHaveTextContent("#1 bug");
  });

  it("http tools render METHOD url with the http status; 4xx/5xx are errors", () => {
    show([
      ev("tool_call", { id: "h", name: "get_weather", args: { city: "SF" } }),
      ev("tool_result", {
        id: "h", name: "get_weather", output: "{\"t\": 18}", is_error: false,
        source: "http", method: "GET", url: "https://api.example.com/weather", http_status: 200,
      }),
      ev("tool_call", { id: "h2", name: "get_weather", args: {} }),
      ev("tool_result", { id: "h2", name: "get_weather", output: "nope", source: "http", http_status: 503 }),
    ]);
    const [ok, bad] = Array.from(document.querySelectorAll('[data-tool="get_weather"]')) as HTMLElement[];
    expect(ok).toHaveTextContent("● get_weather(GET https://api.example.com/weather)");
    expect(within(ok).getByTestId("http-status")).toHaveTextContent("200");
    expect(ok.dataset.error).toBeUndefined();
    expect(bad.dataset.error).toBe("true");
    expect(within(bad).getByTestId("http-status")).toHaveTextContent("503");
  });
});
