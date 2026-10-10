import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import DiffView, { classifyDiffLines } from "../../src/components/DiffView";

const DIFF = [
  "diff --git a/hello.py b/hello.py",
  "new file mode 100644",
  "index 0000000..e69de29",
  "--- /dev/null",
  "+++ b/hello.py",
  "@@ -0,0 +1,2 @@",
  "+print('hello from forge')",
  "-old line",
  " context line",
  "--- removed sql comment",
  "+++ added triple plus",
].join("\n");

describe("classifyDiffLines", () => {
  it("classifies headers, hunks, additions, deletions and context", () => {
    expect(classifyDiffLines(DIFF.split("\n"))).toEqual([
      "meta",
      "meta",
      "meta",
      "file",
      "file",
      "hunk",
      "add",
      "del",
      "context",
      "del", // inside a hunk, '---' is content, not a file header
      "add",
    ]);
  });

  it("resets to header parsing at the next file", () => {
    const kinds = classifyDiffLines(["@@ -1 +1 @@", "-a", "diff --git a/x b/x", "--- a/x", "+++ b/x", "@@ -1 +1 @@", "+b"]);
    expect(kinds).toEqual(["hunk", "del", "meta", "file", "file", "hunk", "add"]);
  });

  it("treats empty and plain lines as context", () => {
    expect(classifyDiffLines(["", "plain"])).toEqual(["context", "context"]);
  });
});

describe("DiffView", () => {
  it("renders one row per line with its kind and color", () => {
    render(<DiffView diff={DIFF + "\n"} />);
    const view = screen.getByTestId("diff-view");
    const rows = view.querySelectorAll("[data-kind]");
    expect(rows).toHaveLength(11); // trailing newline does not add an empty row
    const added = screen.getByText("+print('hello from forge')");
    expect(added).toHaveAttribute("data-kind", "add");
    expect(added.className).toContain("bg-emerald-50");
    expect(screen.getByText("-old line").className).toContain("bg-red-50");
    expect(screen.getByText("@@ -0,0 +1,2 @@").className).toContain("bg-accent-50");
  });

  it("renders blank lines as a non-empty row so they keep height", () => {
    render(<DiffView diff={"@@ -1 +1 @@\n\n+x"} />);
    const rows = screen.getByTestId("diff-view").querySelectorAll("[data-kind]");
    expect(rows[1].textContent).toBe(" ");
  });
});
