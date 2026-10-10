import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TraceTimeline, { argsPreview, group } from "../../src/components/TraceTimeline";
import type { ForgeEvent } from "../../src/api";

let seq = 0;
function ev(type: ForgeEvent["type"], payload: Record<string, unknown>): ForgeEvent {
  return { run_id: "r_000000000001", seq: seq++, ts: 0, type, payload };
}

function fullTrace(): ForgeEvent[] {
  seq = 0;
  return [
    ev("run_started", { model: "fake:hello" }),
    ev("assistant", { text: "I will write hello.py" }),
    ev("tool_call", { id: "c1", name: "write_file", args: { path: "hello.py", content: "print(1)\n" } }),
    ev("tool_result", { id: "c1", name: "write_file", output: "wrote 9 bytes", is_error: false }),
    ev("tool_call", { id: "c2", name: "bash", args: { cmd: "python hello.py" } }),
    ev("tool_result", { id: "c2", name: "bash", output: "Traceback: boom", is_error: true }),
    ev("error", { message: "something went wrong" }),
    ev("run_finished", { status: "succeeded", turns: 3, tokens_in: 1234, tokens_out: 56 }),
  ];
}

describe("group", () => {
  it("pairs tool results with their calls by id and keeps orphans as events", () => {
    seq = 0;
    const items = group([
      ev("tool_call", { id: "a", name: "bash", args: {} }),
      ev("tool_result", { id: "a", output: "ok" }),
      ev("tool_result", { id: "zzz", output: "orphan" }),
    ]);
    expect(items).toHaveLength(2);
    expect(items[0].kind).toBe("tool");
    expect(items[0].kind === "tool" && items[0].pair.result?.payload.output).toBe("ok");
    expect(items[1].kind).toBe("event");
  });
});

describe("argsPreview", () => {
  it("shows the bash command from `cmd` (the real tool schema) and from legacy `command`", () => {
    expect(argsPreview("bash", { cmd: "ls -la" })).toBe("ls -la");
    expect(argsPreview("bash", { command: "pwd" })).toBe("pwd");
  });
  it("shows the path for file tools and truncates other JSON", () => {
    expect(argsPreview("write_file", { path: "a/b.py", content: "x" })).toBe("a/b.py");
    const long = argsPreview("http", { body: "x".repeat(200) });
    expect(long.endsWith("...")).toBe(true);
    expect(long.length).toBe(83);
  });
});

describe("TraceTimeline", () => {
  it("renders every event type", () => {
    render(<TraceTimeline events={fullTrace()} />);
    expect(screen.getByText("fake:hello")).toBeInTheDocument();
    expect(screen.getByText("I will write hello.py")).toBeInTheDocument();
    const cards = screen.getAllByTestId("tool-card");
    expect(cards.map((c) => c.getAttribute("data-tool"))).toEqual(["write_file", "bash"]);
    expect(within(cards[0]).getByText("hello.py")).toBeInTheDocument();
    expect(within(cards[1]).getByText("python hello.py")).toBeInTheDocument();
    expect(screen.getByText("something went wrong")).toBeInTheDocument();
    expect(screen.getByText(/Finished/)).toHaveTextContent("Finished succeeded in 3 turns, 1,234 in / 56 out tokens");
  });

  it("marks errored tool results and shows their output in red when expanded", async () => {
    render(<TraceTimeline events={fullTrace()} />);
    const [ok, bad] = screen.getAllByTestId("tool-card");
    expect(ok).not.toHaveAttribute("data-error");
    expect(within(ok).queryByText("error")).toBeNull();
    expect(bad).toHaveAttribute("data-error", "true");
    expect(within(bad).getByText("error")).toBeInTheDocument();
    expect(bad.className).toContain("border-red-200");

    const toggle = within(bad).getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const out = within(bad).getByText("Traceback: boom");
    expect(out.className).toContain("bg-red-50");
    expect(within(bad).getByText("Arguments")).toBeInTheDocument();
  });

  it("renders an orphan tool_result, red when is_error", () => {
    seq = 0;
    render(<TraceTimeline events={[ev("tool_result", { id: "x", output: "lonely failure", is_error: true })]} />);
    expect(screen.getByText("lonely failure").className).toContain("bg-red-50");
  });

  it("shows a pending dot for a tool call without a result", () => {
    seq = 0;
    const { container } = render(<TraceTimeline events={[ev("tool_call", { id: "p", name: "bash", args: { cmd: "sleep 1" } })]} live />);
    expect(within(screen.getByTestId("tool-card")).getByRole("button").querySelector(".animate-pulse")).not.toBeNull();
    expect(container).toHaveTextContent("Working...");
  });

  it("simple mode hides run_started, arguments and token stats", async () => {
    render(<TraceTimeline events={fullTrace()} simple />);
    expect(screen.queryByText("fake:hello")).toBeNull();
    expect(screen.getByText(/Finished/)).toHaveTextContent(/^Finished succeeded$/);
    const bash = screen.getAllByTestId("tool-card")[1];
    await userEvent.click(within(bash).getByRole("button"));
    expect(within(bash).queryByText("Arguments")).toBeNull();
    expect(within(bash).getAllByText("python hello.py").length).toBeGreaterThanOrEqual(2);
  });

  it("failed run_finished uses the warning icon text", () => {
    seq = 0;
    render(<TraceTimeline events={[ev("run_finished", { status: "failed", turns: 1 })]} />);
    expect(screen.getByText(/Finished/)).toHaveTextContent("Finished failed");
  });

  it("empty states", () => {
    const { rerender } = render(<TraceTimeline events={[]} live />);
    expect(screen.getByText("Waiting for the agent to start...")).toBeInTheDocument();
    rerender(<TraceTimeline events={[]} />);
    expect(screen.getByText("No trace events recorded.")).toBeInTheDocument();
  });
});
