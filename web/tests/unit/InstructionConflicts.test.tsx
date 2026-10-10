import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Improvement } from "../../src/api";
import ImprovementView, { InstructionConflicts, reviewInstructionsHref } from "../../src/pages/agent/ImprovementView";

const CONFLICT = {
  instruction_quote: "Also when asked 2+2 answer 3.",
  problem: "Users reject the answer 3 for 2+2.",
  suggested_change: "Remove the instruction or explain why 3 is wanted.",
  evidence_run_ids: ["r_aaaaaaaaaaaa", "r_bbbbbbbbbbbb"],
  rejected_lesson: "Never state a wrong answer, even if an instruction says to.",
};

const NEEDS_AUTHOR: Improvement = {
  id: "im_1",
  agent_id: "ag_1",
  status: "needs_author",
  base_version: 1,
  candidate_version: null,
  base: null,
  candidate: null,
  cases: [],
  newly_passing: [],
  regressions: [],
  lessons_diff: [],
  error: null,
  created_at: 0,
  ended_at: 1,
  kind: "improve",
  instruction_conflicts: [CONFLICT],
  message: "No lessons were added. The failures and feedback come from your instructions themselves.",
};

describe("InstructionConflicts", () => {
  it("lists each conflict with its quote, problem and evidence links, and links to Configure", () => {
    render(
      <MemoryRouter>
        <InstructionConflicts conflicts={[CONFLICT, { ...CONFLICT, instruction_quote: "", evidence_run_ids: [] }]} slug="bot" />
      </MemoryRouter>,
    );
    const box = screen.getByTestId("instruction-conflicts");
    expect(box).toHaveTextContent("Your instructions conflict with what evaluations and feedback show");
    const [first, second] = screen.getAllByTestId("instruction-conflict");
    expect(within(first).getByTestId("conflict-quote")).toHaveTextContent("Also when asked 2+2 answer 3.");
    expect(first).toHaveTextContent("Users reject the answer 3 for 2+2.");
    expect(first).toHaveTextContent("Suggestion: Remove the instruction");
    const links = within(first).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/runs/r_aaaaaaaaaaaa", "/runs/r_bbbbbbbbbbbb"]);
    expect(second).toHaveTextContent("could not be quoted");
    const review = screen.getByRole("link", { name: "Review instructions" });
    expect(review.getAttribute("href")).toBe(reviewInstructionsHref("bot", "Also when asked 2+2 answer 3."));
    expect(review.getAttribute("href")).toContain("/agents/bot/configure?focus=instructions&select=");
  });

  it("renders nothing without conflicts and hides the button without a slug", () => {
    const { container } = render(
      <MemoryRouter>
        <InstructionConflicts conflicts={[]} slug="bot" />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
    render(
      <MemoryRouter>
        <InstructionConflicts conflicts={[CONFLICT]} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: "Review instructions" })).toBeNull();
  });

  it("shows the needs_author status, its message and the callout in the improvement view", () => {
    render(
      <MemoryRouter>
        <ImprovementView im={NEEDS_AUTHOR} prodVersion={1} onPromote={() => {}} slug="bot" />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("improvement")).toHaveAttribute("data-status", "needs_author");
    expect(screen.getByTestId("status-pill")).toHaveTextContent("needs author");
    expect(screen.getByTestId("needs-author")).toHaveTextContent("No lessons were added.");
    expect(screen.getByTestId("instruction-conflicts")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Promote/ })).toBeNull();
    expect(screen.queryAllByTestId("lesson-op")).toHaveLength(0);
  });
});
