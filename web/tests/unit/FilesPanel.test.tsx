import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FilesPanel, { buildTree } from "../../src/components/FilesPanel";
import { highlight, languageFor } from "../../src/lib/highlight";
import type { WorkspaceFile } from "../../src/api";

const FILES: WorkspaceFile[] = [
  { path: "README.md", size: 12, is_text: true, changed: false },
  { path: "hello.py", size: 26, is_text: true, changed: true, change: "added" },
  { path: "src/app.py", size: 30, is_text: true, changed: true, change: "modified" },
  { path: "src/lib/util.py", size: 10, is_text: true, changed: false },
  { path: "logo.png", size: 2048, is_text: false, changed: false },
];
const CONTENT: Record<string, string> = {
  "hello.py": "# greet\nprint('hello from forge')\n",
  "src/app.py": "def main():\n    return 42\n",
  "README.md": "# Readme\n",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const text = (body: string) => new Response(body, { status: 200, headers: { "content-type": "text/plain" } });

function mockServer(files: Record<string, WorkspaceFile[] | number>) {
  const fn = vi.fn(async (url: string) => {
    const m = url.match(/^\/v1\/runs\/([^/?]+)\/files(?:\/([^?]*))?(\?.*)?$/);
    if (!m) return json({ detail: "nope" }, 404);
    const [, run, path] = m;
    const list = files[run];
    if (typeof list === "number") return json({ detail: "boom" }, list);
    if (!path) return json(list);
    const p = decodeURIComponent(path);
    if (p === "big.txt") return json({ detail: "too large" }, 413);
    return CONTENT[p] !== undefined ? text(CONTENT[p]) : json({ detail: "missing" }, 404);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("highlight", () => {
  it("detects languages from paths", () => {
    expect(languageFor("a/b/x.py")).toBe("python");
    expect(languageFor("web/App.tsx")).toBe("js");
    expect(languageFor("Makefile")).toBe("shell");
    expect(languageFor("notes")).toBeNull();
  });

  it("tokenizes comments, strings, numbers and keywords per line", () => {
    const lines = highlight("def f(x):  # doc\n    return 'a' + 42\n", "python");
    expect(lines).toHaveLength(2);
    const kinds = (i: number) => lines[i].filter((t) => t.kind).map((t) => [t.kind, t.text]);
    expect(kinds(0)).toEqual([["keyword", "def"], ["comment", "# doc"]]);
    expect(kinds(1)).toEqual([["keyword", "return"], ["string", "'a'"], ["number", "42"]]);
    expect(lines[1].map((t) => t.text).join("")).toBe("    return 'a' + 42");
  });

  it("splits multi-line strings across lines and keeps plain text for unknown languages", () => {
    const lines = highlight('x = """a\nb"""\n', "python");
    expect(lines[0].at(-1)).toEqual({ kind: "string", text: '"""a' });
    expect(lines[1][0]).toEqual({ kind: "string", text: 'b"""' });
    expect(highlight("<b>hi</b>\n\nend", null)).toEqual([[{ kind: null, text: "<b>hi</b>" }], [], [{ kind: null, text: "end" }]]);
  });
});

describe("buildTree", () => {
  it("nests directories, sorts them and counts changed files below", () => {
    const t = buildTree(FILES);
    expect(t.changed).toBe(2);
    expect(t.dirs.map((d) => d.name)).toEqual(["src"]);
    expect(t.dirs[0].changed).toBe(1);
    expect(t.dirs[0].dirs[0].files.map((f) => f.path)).toEqual(["src/lib/util.py"]);
    expect(t.files.map((f) => f.path)).toEqual(["hello.py", "logo.png", "README.md"]);
  });
});

const treeFile = (path: string) => screen.getAllByTestId("tree-file").find((el) => el.dataset.path === path)!;

describe("FilesPanel", () => {
  it("lists files, highlights changed ones and opens the first changed file with line numbers", async () => {
    const f = mockServer({ r_1: FILES });
    render(<FilesPanel runId="r_1" />);
    expect(await screen.findByText("5 files, 2 changed")).toBeInTheDocument();
    expect(f.mock.calls[0][0]).toBe("/v1/runs/r_1/files?changed=1");

    expect(treeFile("hello.py")).toHaveAttribute("data-changed", "true");
    expect(treeFile("hello.py")).toHaveTextContent("A");
    expect(treeFile("README.md")).toHaveAttribute("data-changed", "false");

    const view = await screen.findByTestId("file-content");
    expect(view).toHaveAttribute("data-path", "hello.py");
    expect(view.querySelectorAll("tr")).toHaveLength(2);
    expect(within(view.querySelector('[data-line="2"]') as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(view.querySelector('[data-token="comment"]')).toHaveTextContent("# greet");
    expect(view.querySelector('[data-token="string"]')).toHaveTextContent("'hello from forge'");
  });

  it("collapses and expands directories and opens nested files", async () => {
    mockServer({ r_1: FILES });
    const user = userEvent.setup();
    render(<FilesPanel runId="r_1" />);
    const src = await screen.findByRole("treeitem", { name: "src" });
    expect(src).toHaveAttribute("aria-expanded", "true");
    await user.click(within(src).getAllByTestId("tree-dir")[0]);
    expect(src).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("app.py")).not.toBeInTheDocument();
    await user.click(within(src).getAllByTestId("tree-dir")[0]);
    await user.click(screen.getByText("app.py"));
    await waitFor(() => expect(screen.getByTestId("file-content")).toHaveAttribute("data-path", "src/app.py"));
    expect(screen.getByTestId("file-content").querySelector('[data-token="keyword"]')).toHaveTextContent("def");
  });

  it("filters to changed files and shows binary files without fetching", async () => {
    const f = mockServer({ r_1: FILES });
    const user = userEvent.setup();
    render(<FilesPanel runId="r_1" />);
    await screen.findByTestId("file-content");
    const calls = f.mock.calls.length;
    await user.click(screen.getByText("logo.png"));
    expect(screen.getByTestId("file-binary")).toBeInTheDocument();
    expect(f.mock.calls.length).toBe(calls);
    await user.click(screen.getByLabelText("Changed only"));
    expect(screen.getAllByTestId("tree-file").map((el) => el.dataset.path)).toEqual(["src/app.py", "hello.py"]); // folders first
  });

  it("shows the empty state, the no workspace state and other errors", async () => {
    mockServer({ r_empty: [], r_none: 404, r_bad: 500 });
    const { rerender } = render(<FilesPanel runId="r_empty" />);
    expect(await screen.findByTestId("files-empty")).toHaveTextContent("The workspace is empty.");
    rerender(<FilesPanel runId="r_none" />);
    expect(await screen.findByTestId("files-error")).toHaveTextContent("This run has no saved workspace yet.");
    rerender(<FilesPanel runId="r_bad" />);
    await waitFor(() => expect(screen.getByTestId("files-error")).toHaveTextContent("boom"));
  });

  it("reloads when runId changes and reports oversized files", async () => {
    const f = mockServer({
      r_1: FILES,
      r_2: [{ path: "big.txt", size: 2_000_000, is_text: true, changed: true, change: "added" }],
    });
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<FilesPanel runId="r_1" onClose={onClose} />);
    await screen.findByText("5 files, 2 changed");
    rerender(<FilesPanel runId="r_2" onClose={onClose} />);
    expect(await screen.findByText("1 file, 1 changed")).toBeInTheDocument();
    expect(f.mock.calls.some((c) => c[0] === "/v1/runs/r_2/files?changed=1")).toBe(true);
    expect(await screen.findByRole("alert")).toHaveTextContent("larger than 1 MB");
    await user.click(screen.getByLabelText("Close files"));
    expect(onClose).toHaveBeenCalled();
  });

  it("passes the share token", async () => {
    const f = mockServer({ r_1: FILES });
    render(<FilesPanel runId="r_1" share="tok" />);
    await screen.findByTestId("file-content");
    expect(f.mock.calls[0][0]).toBe("/v1/runs/r_1/files?changed=1&share=tok");
  });
});
