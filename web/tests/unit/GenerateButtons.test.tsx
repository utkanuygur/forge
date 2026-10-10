import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { EvalCaseSuggestion } from "../../src/api";
import GenerateCases, { CaseReviewList } from "../../src/components/GenerateCases";
import InstructionsAssist from "../../src/components/InstructionsAssist";
import { addSuggestedRules } from "../../src/components/PolicyEditor";
import SaveAsEval from "../../src/components/SaveAsEval";
import { lineDiff } from "../../src/lib/textDiff";
import { AddCaseForm } from "../../src/pages/agent/EvalsTab";
import { SeedFileModal } from "../../src/pages/agent/ConfigureTab";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const SHELL = 'set -euo pipefail\nfail() { echo "FAIL: $*"; exit 1; }\ntest -f out.txt || fail "no out.txt"\necho PASS';
const mk = (name: string, split: "train" | "holdout", extra: Partial<EvalCaseSuggestion> = {}): EvalCaseSuggestion => ({
  name,
  input: `Do the ${name} task.`,
  checker_type: "shell",
  checker: SHELL,
  split,
  rationale: `Covers ${name}.`,
  ...extra,
});

let fetchMock: ReturnType<typeof vi.fn>;
let replies: Record<string, unknown>;
const bodies = (url: string) =>
  fetchMock.mock.calls.filter(([u]) => u === url).map(([, init]) => JSON.parse((init as RequestInit).body as string));

beforeEach(() => {
  replies = {};
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url in replies) return json(replies[url]);
    if (url.endsWith("/evals") && init?.method === "POST") return json({ id: "ec_1", ...JSON.parse(init.body as string) });
    if (url.endsWith("/save-as-eval")) return json({ id: "ec_2", name: "saved", split: "train" });
    return json({ detail: `unexpected ${url}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("lineDiff and addSuggestedRules", () => {
  it("marks removed and added lines and keeps unchanged ones", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc\nd").split("\n")).toEqual(["@@ instructions @@", " a", "+B", "-b", " c", "+d"]);
  });
  it("appends suggested rules without duplicates and maps an empty pattern to none", () => {
    const p = addSuggestedRules({ rules: [{ tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask" }] }, [
      { tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask", label: "git push" },
      { tool: "write_file", pattern: "", mode: "deny", label: "any write" },
    ]);
    expect(p.rules).toEqual([
      { tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask" },
      { tool: "write_file", pattern: null, mode: "deny", label: "any write" },
    ]);
  });
});

describe("CaseReviewList", () => {
  it("checks every draft by default, toggles, expands the checker and adds only the checked ones", async () => {
    const onAdd = vi.fn();
    render(<CaseReviewList cases={[mk("one", "train"), mk("two", "holdout")]} onAdd={onAdd} />);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByRole("button", { name: "Add selected (2)" })).toBeEnabled();
    await userEvent.click(screen.getByLabelText("two"));
    await userEvent.click(within(screen.getAllByTestId("review-case")[0]).getByRole("button", { name: "Checker" }));
    expect(screen.getByText(/set -euo pipefail/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add selected (1)" }));
    expect(onAdd).toHaveBeenCalledWith([mk("one", "train")]);
  });
});

describe("GenerateCases", () => {
  it("asks for N cases with the chosen holdout count, creates nothing until Add selected, then posts each", async () => {
    replies["/v1/assist/eval_cases"] = { result: { cases: [mk("a", "train"), mk("b", "train"), mk("c", "holdout"), mk("d", "holdout")] }, rationale: "r" };
    const onAdded = vi.fn();
    render(<GenerateCases slug="demo" onAdded={onAdded} />);
    await userEvent.selectOptions(screen.getByLabelText("How many cases"), "4");
    await userEvent.selectOptions(screen.getByLabelText("Holdout cases"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Generate cases" }));
    await screen.findByRole("button", { name: "Add selected (4)" });
    expect(bodies("/v1/assist/eval_cases")[0]).toEqual({ slug: "demo", input: { n: 4, holdout: 2 }, avoid: [] });
    expect(bodies("/v1/agents/demo/evals")).toEqual([]);
    await userEvent.click(screen.getByLabelText("b"));
    await userEvent.click(screen.getByRole("button", { name: "Add selected (3)" }));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith(3));
    expect(bodies("/v1/agents/demo/evals").map((b) => b.name)).toEqual(["a", "c", "d"]);
    expect(bodies("/v1/agents/demo/evals")[1]).toEqual({ name: "c", input: "Do the c task.", checker_type: "shell", checker: SHELL, split: "holdout" });
  });
});

describe("AddCaseForm", () => {
  it("prefills a generated draft and only saves on Add case", async () => {
    const onDone = vi.fn();
    render(<AddCaseForm slug="demo" onDone={onDone} initial={mk("draft", "holdout", { checker_type: "judge", checker: "Pass only if" })} />);
    expect(screen.getByLabelText("Name")).toHaveValue("draft");
    expect(screen.getByLabelText("Split")).toHaveValue("holdout");
    expect(screen.getByLabelText("Checker")).toHaveValue("Pass only if");
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Add case" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(bodies("/v1/agents/demo/evals")[0]).toMatchObject({ name: "draft", split: "holdout", checker_type: "judge" });
  });

  it("Generate on Input fills input, name and an empty checker; Generate on Checker sends the input as task", async () => {
    replies["/v1/assist/eval_case"] = { result: mk("gen", "train"), rationale: "r" };
    render(<AddCaseForm slug="demo" onDone={() => {}} />);
    const checkerButton = screen.getByRole("button", { name: "Generate grading for this case" });
    expect(checkerButton).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Generate a task for this case" }));
    await waitFor(() => expect(screen.getByLabelText("Input")).toHaveValue("Do the gen task."));
    expect(screen.getByLabelText("Name")).toHaveValue("gen");
    expect(screen.getByLabelText("Checker")).toHaveValue(SHELL);
    expect(bodies("/v1/assist/eval_case")[0].input).toEqual({ split: "train", checker_type: "shell" });

    replies["/v1/assist/eval_case"] = { result: mk("gen", "train", { checker: "set -euo pipefail\necho FAIL; exit 1" }), rationale: "r" };
    await userEvent.click(checkerButton);
    await waitFor(() => expect(screen.getByLabelText("Checker")).toHaveValue("set -euo pipefail\necho FAIL; exit 1"));
    expect(bodies("/v1/assist/eval_case")[1].input).toEqual({ task: "Do the gen task.", checker_type: "shell", split: "train" });
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByLabelText("Checker")).toHaveValue(SHELL);
    expect(bodies("/v1/agents/demo/evals")).toEqual([]);
  });
});

describe("SaveAsEval", () => {
  it("drafts the case from the run and saves the edited draft", async () => {
    replies["/v1/assist/eval_case"] = { result: mk("from-run", "holdout", { checker_type: "judge", checker: "Pass only if every point holds:\n1. a\n2. b" }), rationale: "r" };
    render(<SaveAsEval runId="r_1" slug="demo" />);
    await userEvent.click(screen.getByRole("button", { name: "holdout" }));
    await userEvent.click(screen.getByRole("button", { name: "Generate a test case from this run" }));
    await waitFor(() => expect(screen.getByLabelText("Case name")).toHaveValue("from-run"));
    expect(bodies("/v1/assist/eval_case")[0].input).toEqual({ from_run_id: "r_1", split: "holdout" });
    await userEvent.type(screen.getByLabelText("Checker"), "\n3. c");
    await userEvent.click(screen.getByRole("button", { name: "Save as test case" }));
    await waitFor(() => expect(bodies("/v1/runs/r_1/save-as-eval")).toHaveLength(1));
    expect(bodies("/v1/runs/r_1/save-as-eval")[0]).toEqual({
      split: "holdout",
      name: "from-run",
      checker_type: "judge",
      checker: "Pass only if every point holds:\n1. a\n2. b\n3. c",
    });
  });

  it("without a slug keeps the plain save", () => {
    render(<SaveAsEval runId="r_1" />);
    expect(screen.queryByRole("button", { name: "Generate a test case from this run" })).toBeNull();
  });
});

function Instructions() {
  const [text, setText] = useState("You fix bugs.\nRun the tests.");
  return (
    <div>
      <InstructionsAssist slug="demo" value={text} onChange={setText} />
      <textarea aria-label="Instructions" value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}

describe("InstructionsAssist", () => {
  const improved = "You fix bugs.\nRun the tests.\nAlways finish with a one line summary of what you changed.";
  beforeEach(() => {
    replies["/v1/assist/instructions"] = { result: { instructions: improved, summary: "Adds a closing summary requirement." }, rationale: "r" };
  });

  it("shows a diff; Accept replaces the text and Undo restores it", async () => {
    render(<Instructions />);
    await userEvent.click(screen.getByRole("button", { name: "Generate an improved prompt" }));
    const diff = await screen.findByTestId("diff-view");
    expect(within(diff).getByText("+Always finish with a one line summary of what you changed.")).toHaveAttribute("data-kind", "add");
    expect(screen.getByText(/Adds a closing summary requirement/)).toBeInTheDocument();
    expect(bodies("/v1/assist/instructions")[0].input).toEqual({ current: "You fix bugs.\nRun the tests." });
    expect(screen.getByLabelText("Instructions")).toHaveValue("You fix bugs.\nRun the tests.");
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByLabelText("Instructions")).toHaveValue(improved);
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByLabelText("Instructions")).toHaveValue("You fix bugs.\nRun the tests.");
  });

  it("Reject discards the suggestion", async () => {
    render(<Instructions />);
    await userEvent.click(screen.getByRole("button", { name: "Generate an improved prompt" }));
    await screen.findByTestId("diff-view");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.queryByTestId("diff-view")).toBeNull();
    expect(screen.getByLabelText("Instructions")).toHaveValue("You fix bugs.\nRun the tests.");
  });
});

describe("SeedFileModal", () => {
  it("Generate previews path and content; the file is added only on Save file", async () => {
    replies["/v1/assist/seed_file"] = { result: { path: "data/orders.csv", content: "id,total\n1,10\n", description: "Ten orders." }, rationale: "r" };
    const onSave = vi.fn();
    render(<SeedFileModal slug="demo" existing={["README.md"]} initialPath="" initialContent="" onSave={onSave} onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText("Describe a sample file (optional)"), "orders csv");
    await userEvent.click(screen.getByRole("button", { name: "Generate a sample file" }));
    await waitFor(() => expect(screen.getByLabelText("Path")).toHaveValue("data/orders.csv"));
    expect(screen.getByLabelText("Content")).toHaveValue("id,total\n1,10\n");
    expect(bodies("/v1/assist/seed_file")[0].input).toEqual({ description: "orders csv", existing: ["README.md"] });
    expect(onSave).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save file" }));
    expect(onSave).toHaveBeenCalledWith("data/orders.csv", "id,total\n1,10\n");
  });
});
