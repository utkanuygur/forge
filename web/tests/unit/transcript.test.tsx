import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Transcript from "../../src/components/transcript/Transcript";
import Markdown from "../../src/components/transcript/Markdown";
import {
  buildItems,
  diffStats,
  lineDiff,
  parseBash,
  toolLabel,
  withContext,
} from "../../src/components/transcript/model";
import type { ForgeEvent, Run } from "../../src/api";

let seq = 0;
function ev(type: ForgeEvent["type"], payload: Record<string, unknown>, ts = 100): ForgeEvent {
  return { run_id: "r_000000000001", seq: seq++, ts, type, payload };
}
const bashOut = (code: number, stdout: string, stderr = "") =>
  `exit_code: ${code}\n--- stdout ---\n${stdout}\n--- stderr ---\n${stderr}`;

function tool(name: string, args: Record<string, unknown>, output?: string, isError = false): ForgeEvent[] {
  const id = `c${seq}`;
  const call = ev("tool_call", { id, name, args });
  return output === undefined ? [call] : [call, ev("tool_result", { id, name, output, is_error: isError })];
}

function card(name: string) {
  return document.querySelector(`[data-testid="tool-card"][data-tool="${name}"]`) as HTMLElement;
}

beforeEach(() => {
  seq = 0;
});

// ---------------------------------------------------------------- model

describe("model", () => {
  it("parses bash output, including timeouts", () => {
    expect(parseBash(bashOut(2, "a\nb", "err"))).toEqual({ exitCode: 2, stdout: "a\nb", stderr: "err", timedOut: false });
    const t = parseBash(bashOut(-9, "") + "\n[command timed out after 5s and was killed]");
    expect(t.timedOut).toBe(true);
    expect(t.exitCode).toBe(-9);
    expect(parseBash("free text").stdout).toBe("free text");
  });

  it("labels tools like Claude Code", () => {
    expect(toolLabel("bash", { cmd: "python hello.py" })).toEqual({ tool: "Bash", arg: "python hello.py" });
    expect(toolLabel("bash", { cmd: "a\nb" }).arg).toBe("a …");
    expect(toolLabel("bash", { cmd: "x".repeat(200) }).arg.length).toBe(80);
    expect(toolLabel("read_file", { path: "a.py", start: 3, end: 9 })).toEqual({ tool: "Read", arg: "a.py:3-9" });
    expect(toolLabel("read_file", { path: "a.py" }).arg).toBe("a.py");
    expect(toolLabel("write_file", { path: "w.py" })).toEqual({ tool: "Write", arg: "w.py" });
    expect(toolLabel("edit_file", { path: "e.py" })).toEqual({ tool: "Update", arg: "e.py" });
    expect(toolLabel("http_get", { q: 1 })).toEqual({ tool: "http_get", arg: '{"q":1}' });
  });

  it("computes a line diff with numbers and stats", () => {
    const d = lineDiff("a\nb\nc\nd\n", "a\nB\nc\nd\ne\n");
    expect(d.map((l) => `${l.kind}:${l.text}`)).toEqual(["same:a", "del:b", "add:B", "same:c", "same:d", "add:e"]);
    expect(diffStats(d)).toEqual({ added: 2, removed: 1 });
    expect(d[2]).toMatchObject({ newN: 2 });
    expect(d[1]).toMatchObject({ oldN: 2 });
    expect(diffStats(lineDiff("", "x\ny"))).toEqual({ added: 2, removed: 0 });
    expect(diffStats(lineDiff("same", "same"))).toEqual({ added: 0, removed: 0 });
  });

  it("collapses unchanged runs to context with gap markers", () => {
    const old = Array.from({ length: 20 }, (_, i) => `l${i}`).join("\n");
    const neu = old.replace("l10", "L10");
    const shown = withContext(lineDiff(old, neu), 2);
    expect(shown[0]).toBeNull();
    expect(shown.filter(Boolean).map((l) => l!.text)).toEqual(["l8", "l9", "l10", "L10", "l11", "l12"]);
    expect(shown[shown.length - 1]).toBeNull();
  });

  it("groups calls with results and classifies narration", () => {
    const items = buildItems([
      ev("assistant", { text: "plan", kind: "progress" }),
      ...tool("bash", { cmd: "ls" }, bashOut(0, "x")),
      ev("assistant", { text: "legacy final" }), // no kind: final because no tool call follows
      ev("assistant", { text: "   " }),
    ]);
    expect(items.map((i) => i.kind)).toEqual(["narration", "tool", "narration"]);
    expect(items[2]).toMatchObject({ final: true });
    expect(items[0]).toMatchObject({ final: false });
  });
});

// ---------------------------------------------------------------- tool renderers

describe("tool renderers", () => {
  it("Bash: header, first 4 lines, expand, exit code badge", async () => {
    const lines = Array.from({ length: 10 }, (_, i) => `line ${i + 1}`).join("\n");
    render(<Transcript live={false} events={tool("bash", { cmd: "python hello.py" }, bashOut(0, lines))} />);
    const c = card("bash");
    expect(c).toHaveTextContent("Bash(python hello.py)");
    expect(c).toHaveTextContent("line 4");
    expect(c).not.toHaveTextContent("line 5");
    expect(c).toHaveTextContent("… +6 lines (click to expand)");
    expect(c.dataset.status).toBe("ok");
    await userEvent.click(within(c).getByTestId("more-lines"));
    expect(c).toHaveTextContent("line 10");
  });

  it("Bash: non-zero exit is red with a badge; timeouts are noted; empty output", () => {
    render(
      <Transcript
        live={false}
        events={[
          ...tool("bash", { cmd: "false" }, bashOut(1, "", "boom"), true),
          ...tool("bash", { cmd: "sleep 9" }, bashOut(-9, "") + "\n[command timed out after 5s and was killed]", true),
          ...tool("bash", { cmd: "true" }, bashOut(0, "")),
        ]}
      />,
    );
    const [failed, slow, quiet] = Array.from(document.querySelectorAll('[data-tool="bash"]')) as HTMLElement[];
    expect(failed.dataset.error).toBe("true");
    expect(within(failed).getByTestId("exit-code")).toHaveTextContent("exit 1");
    expect(failed).toHaveTextContent("boom");
    expect(slow).toHaveTextContent("Timed out");
    expect(quiet).toHaveTextContent("(No content)");
  });

  it("Read: line count, expandable with line numbers", async () => {
    render(
      <Transcript live={false} events={tool("read_file", { path: "app.py", start: 2, end: 3 }, "2\tdef f():\n3\t    return 1")} />,
    );
    const c = card("read_file");
    expect(c).toHaveTextContent("Read(app.py:2-3)");
    expect(c).toHaveTextContent("Read 2 lines");
    expect(within(c).queryByTestId("numbered-code")).toBeNull();
    await userEvent.click(within(c).getByRole("button", { name: /Read 2 lines/ }));
    const code = within(c).getByTestId("numbered-code");
    expect(code).toHaveTextContent("2");
    expect(code).toHaveTextContent("def f():");
  });

  it("Write: line count, expandable with highlighted content", async () => {
    render(
      <Transcript live={false} events={tool("write_file", { path: "hello.py", content: "def hi():\n    print('x')\n" }, "wrote")} />,
    );
    const c = card("write_file");
    expect(c).toHaveTextContent("Write(hello.py)");
    expect(c).toHaveTextContent("Wrote 2 lines to hello.py");
    await userEvent.click(within(c).getByTestId("tool-header"));
    const code = within(c).getByTestId("numbered-code");
    expect(code.querySelector(".hljs-keyword")).toHaveTextContent("def");
  });

  it("Update: inline red/green diff with counts; failed edits show the error", () => {
    render(
      <Transcript
        live={false}
        events={[
          ...tool("edit_file", { path: "a.py", old: "x = 1\ny = 2\n", new: "x = 1\ny = 3\nz = 4\n" }, "edited a.py"),
          ...tool("edit_file", { path: "b.py", old: "nope", new: "x" }, "`old` text not found in b.py", true),
        ]}
      />,
    );
    const [ok, bad] = Array.from(document.querySelectorAll('[data-tool="edit_file"]')) as HTMLElement[];
    expect(ok).toHaveTextContent("Update(a.py)");
    expect(within(ok).getByTestId("diff-stats")).toHaveTextContent("Added 2 lines, removed 1 line");
    const diff = within(ok).getByTestId("edit-diff");
    expect(diff.querySelector('[data-kind="del"]')).toHaveTextContent("y = 2");
    expect(Array.from(diff.querySelectorAll('[data-kind="add"]')).map((n) => n.textContent)).toEqual([
      "+y = 3",
      "+z = 4",
    ]);
    expect(bad.dataset.error).toBe("true");
    expect(bad).toHaveTextContent("`old` text not found in b.py");
    expect(within(bad).queryByTestId("edit-diff")).toBeNull();
  });

  it("unknown tools: name, JSON args and raw output", async () => {
    render(<Transcript live={false} events={tool("http_get", { url: "u" }, "raw body")} />);
    const c = card("http_get");
    expect(c).toHaveTextContent('http_get({"url":"u"})');
    expect(c).toHaveTextContent("raw body");
  });

  it("pending tool shows a spinner and elapsed time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(103_000));
    try {
      render(<Transcript live events={tool("bash", { cmd: "sleep 5" })} />);
      const c = card("bash");
      expect(c.dataset.status).toBe("pending");
      expect(within(c).getByRole("status", { name: "Running" })).toBeInTheDocument();
      expect(within(c).getByTestId("tool-elapsed")).toHaveTextContent("3s");
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(within(c).getByTestId("tool-elapsed")).toHaveTextContent("5s");
    } finally {
      vi.useRealTimers();
    }
  });

  it("tool output never renders as HTML", () => {
    render(<Transcript live={false} events={tool("bash", { cmd: "cat x" }, bashOut(0, "<script>alert(1)</script><b>x</b>"))} />);
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("b")).toBeNull();
    expect(card("bash")).toHaveTextContent("<script>alert(1)</script>");
  });
});

// ---------------------------------------------------------------- markdown

describe("markdown", () => {
  it("renders headings, lists, bold, inline code and highlighted fences", () => {
    render(<Markdown text={"## Plan\n- **bold** item\n- `code`\n\n```python\ndef f():\n    pass\n```"} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Plan");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(document.querySelector("pre code .hljs-keyword")).toHaveTextContent("def");
  });

  it("sanitizes: raw HTML and javascript: links do not render", () => {
    render(<Markdown text={'Hi <script>alert(1)</script> <img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))'} />);
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByTestId("markdown")).toHaveTextContent("<script>alert(1)</script>");
    const a = screen.getByTestId("markdown").querySelector("a")!;
    expect(a).toHaveTextContent("x");
    expect(a.getAttribute("href") ?? "").not.toContain("javascript");
  });
});

// ---------------------------------------------------------------- transcript

function longRun(): ForgeEvent[] {
  const evs: ForgeEvent[] = [ev("run_started", { model: "m" })];
  for (let i = 0; i < 3; i++) {
    evs.push(ev("assistant", { text: `Step ${i}`, kind: "progress" }));
    evs.push(...tool("bash", { cmd: `echo ${i}` }, bashOut(0, Array.from({ length: 8 }, (_, k) => `out${i}-${k}`).join("\n"))));
  }
  return evs;
}

describe("transcript", () => {
  it("shows narration, final answer and the Done status line", () => {
    const events = [
      ev("run_started", {}, 100),
      ev("assistant", { text: "I'll create hello.py.", kind: "progress" }, 100),
      ...tool("write_file", { path: "hello.py", content: "print(1)\n" }, "wrote"),
      ev("assistant", { text: "**Done**: created it.", kind: "final" }, 110),
      ev("run_finished", { status: "succeeded", turns: 5, tokens_in: 3000, tokens_out: 200, cost_usd: 0.05 }, 116.5),
    ];
    const run = { started_at: 100, ended_at: 116.5, status: "succeeded", turns: 5, tokens_in: 3000, tokens_out: 200 } as Run;
    render(<Transcript live={false} events={events} run={run} input="Write hello.py" />);
    expect(screen.getByTestId("transcript-input")).toHaveTextContent("Write hello.py");
    const narr = screen.getAllByTestId("narration");
    expect(narr[0]).toHaveAttribute("data-kind", "progress");
    expect(narr[1]).toHaveAttribute("data-kind", "final");
    expect(within(narr[1]).getByText("Done").tagName).toBe("STRONG");
    expect(screen.getByTestId("status-line")).toHaveTextContent("Done in 16.5s · 5 turns · 3.2k tokens · $0.05");
  });

  it("running status line counts time and tokens; failed runs show a red error block", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(112_000));
    try {
      const { unmount } = render(
        <Transcript live events={[ev("run_started", {}, 100)]} run={{ started_at: 100, tokens_in: 3000, tokens_out: 200 } as Run} />,
      );
      expect(screen.getByTestId("status-line")).toHaveTextContent("Working… 12s · 3.2k tokens");
      unmount();
    } finally {
      vi.useRealTimers();
    }
    render(
      <Transcript
        live={false}
        events={[ev("error", { message: "model error: boom" }), ev("run_finished", { status: "failed", turns: 1 })]}
      />,
    );
    expect(screen.getByTestId("transcript-error")).toHaveTextContent("model error: boom");
    expect(screen.getByTestId("status-line")).toHaveTextContent("Failed after");
  });

  it("Expand all / Collapse all and Ctrl+O toggle every tool output", async () => {
    render(<Transcript live={false} events={longRun()} />);
    const hidden = () => screen.queryAllByText(/out\d-7/);
    expect(hidden()).toHaveLength(0);
    await userEvent.click(screen.getByTestId("expand-all"));
    expect(hidden()).toHaveLength(3);
    expect(screen.getByTestId("expand-all")).toHaveTextContent("Collapse all");
    await userEvent.click(screen.getByTestId("expand-all"));
    expect(hidden()).toHaveLength(0);
    fireEvent.keyDown(window, { key: "o", ctrlKey: true });
    expect(hidden()).toHaveLength(3);
    fireEvent.keyDown(window, { key: "o", ctrlKey: true });
    expect(hidden()).toHaveLength(0);
  });

  describe("auto-scroll", () => {
    let scrollSpy: ReturnType<typeof vi.fn>;
    const setScroll = (scrollY: number) => {
      Object.defineProperty(window, "scrollY", { value: scrollY, configurable: true });
      Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
      Object.defineProperty(document.documentElement, "scrollHeight", { value: 3000, configurable: true });
      fireEvent.scroll(window);
    };
    beforeEach(() => {
      scrollSpy = vi.fn();
      Element.prototype.scrollIntoView = scrollSpy as unknown as Element["scrollIntoView"];
    });
    afterEach(() => {
      // @ts-expect-error cleanup of the jsdom stub
      delete Element.prototype.scrollIntoView;
    });

    it("follows new steps while live, pauses with a Jump to latest pill when scrolled up", async () => {
      const base = longRun();
      const { rerender } = render(<Transcript live events={base.slice(0, 3)} />);
      expect(scrollSpy).toHaveBeenCalled();
      expect(screen.queryByTestId("jump-latest")).toBeNull();

      act(() => setScroll(100)); // reader scrolls up
      const calls = scrollSpy.mock.calls.length;
      rerender(<Transcript live events={base.slice(0, 5)} />);
      expect(scrollSpy.mock.calls.length).toBe(calls);
      const pill = screen.getByTestId("jump-latest");
      expect(pill).toHaveTextContent("Jump to latest");

      await userEvent.click(pill);
      expect(scrollSpy.mock.calls.length).toBe(calls + 1);
      expect(screen.queryByTestId("jump-latest")).toBeNull();
      rerender(<Transcript live events={base} />);
      expect(scrollSpy.mock.calls.length).toBeGreaterThan(calls + 1);

      act(() => setScroll(2200)); // back at the bottom
      expect(screen.queryByTestId("jump-latest")).toBeNull();
    });

    it("never shows the pill once the run is finished", () => {
      render(<Transcript live={false} events={longRun()} />);
      act(() => setScroll(0));
      expect(screen.queryByTestId("jump-latest")).toBeNull();
    });
  });

  it("caps large outputs until asked for everything", async () => {
    const big = Array.from({ length: 1000 }, (_, i) => `row${i}`).join("\n");
    render(<Transcript live={false} events={tool("bash", { cmd: "seq 1000" }, bashOut(0, big))} />);
    await userEvent.click(screen.getByTestId("tool-header"));
    expect(screen.queryByText(/row999/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show all 1,000 lines" }));
    expect(screen.getByText(/row999/)).toBeInTheDocument();
  });
});
