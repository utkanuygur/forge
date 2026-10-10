import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import ApprovalCard, { approvalState } from "../../src/components/ApprovalCard";
import PolicyEditor, { PRESETS, addPreset, normalizePolicy, parseUsd } from "../../src/components/PolicyEditor";
import TraceTimeline, { budgetWarningText } from "../../src/components/TraceTimeline";
import StatusPill, { statusClasses } from "../../src/components/StatusPill";
import { BudgetBanner, BudgetMeter, PendingApprovalsBadge } from "../../src/components/Governance";
import { isTerminal, type ApprovalPolicy, type ForgeEvent, type Governance } from "../../src/api";

afterEach(() => vi.unstubAllGlobals());

let seq = 0;
function ev(type: ForgeEvent["type"], payload: Record<string, unknown>): ForgeEvent {
  return { run_id: "r_000000000001", seq: seq++, ts: 0, type, payload };
}

const REQUEST = () =>
  ev("approval_requested", {
    id: "ap_1",
    tool_call_id: "c1",
    name: "bash",
    args: { cmd: "rm -rf build" },
    rule: { index: 0, tool: "bash", mode: "ask", description: "rm -rf: bash matching /rm/" },
    expires_at: Date.now() / 1000 + 900,
  });

describe("approvalState", () => {
  it("derives pending, approved, denied and expired", () => {
    seq = 0;
    expect(approvalState(undefined, false)).toBe("pending");
    expect(approvalState(undefined, true)).toBe("expired");
    expect(approvalState(ev("approval_resolved", { decision: "approve", by: "ui" }), false)).toBe("approved");
    expect(approvalState(ev("approval_resolved", { decision: "deny", by: "ui" }), false)).toBe("denied");
    expect(approvalState(ev("approval_resolved", { decision: "deny", by: "timeout" }), false)).toBe("expired");
  });
});

describe("ApprovalCard", () => {
  it("shows the tool, args and matched rule, and posts a denial with the note", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ id: "ap_1" }), { headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ApprovalCard request={REQUEST()} runId="r_1" canApprove />);
    const card = screen.getByTestId("approval-card");
    expect(card).toHaveAttribute("data-state", "pending");
    expect(card).toHaveTextContent("rm -rf build");
    expect(card).toHaveTextContent("rm -rf: bash matching /rm/");
    await userEvent.type(screen.getByLabelText("Reviewer note"), "keep it");
    await userEvent.click(screen.getByRole("button", { name: /Deny/ }));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/v1/runs/r_1/approvals/ap_1");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "deny", note: "keep it" });
    expect(await screen.findByText(/Waiting for the run to continue/)).toBeInTheDocument();
  });

  it("shows server errors and keeps the buttons", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "approval is no longer pending" }), { status: 409, headers: { "content-type": "application/json" } })),
    );
    render(<ApprovalCard request={REQUEST()} runId="r_1" canApprove />);
    await userEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(await screen.findByText("approval is no longer pending")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeEnabled();
  });

  it("has no buttons for viewers who cannot approve, and shows resolutions", () => {
    seq = 0;
    const req = REQUEST();
    const { rerender } = render(<ApprovalCard request={req} runId="r_1" />);
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.getByText(/Waiting for the agent owner/)).toBeInTheDocument();
    rerender(<ApprovalCard request={req} runId="r_1" canApprove resolved={ev("approval_resolved", { id: "ap_1", decision: "deny", note: "no", by: "ui" })} />);
    expect(screen.getByTestId("approval-card")).toHaveAttribute("data-state", "denied");
    expect(screen.getByTestId("approval-card")).toHaveTextContent("Denied (ui): no");
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
  });
});

describe("TraceTimeline governance events", () => {
  it("renders the approval card inline, folds in its resolution, and shows budget warnings", () => {
    seq = 0;
    const events = [
      ev("tool_call", { id: "c1", name: "bash", args: { cmd: "rm -rf build" } }),
      REQUEST(),
      ev("approval_resolved", { id: "ap_1", tool_call_id: "c1", decision: "approve", note: "", by: "ui" }),
      ev("tool_result", { id: "c1", name: "bash", output: "exit_code: 0", is_error: false }),
      ev("budget_warning", { scope: "run", limit_usd: 0.05, spent_usd: 0.04, fraction: 0.8 }),
    ];
    render(<TraceTimeline events={events} runId="r_1" canApprove />);
    const card = screen.getByTestId("approval-card");
    expect(card).toHaveAttribute("data-state", "approved");
    expect(screen.queryByText(/Approval granted/)).toBeNull();
    expect(screen.getByTestId("budget-warning")).toHaveTextContent("This run has used 80% of its spending cap ($0.04 of $0.05).");
  });

  it("simple (share) mode never offers approval buttons", () => {
    seq = 0;
    render(<TraceTimeline events={[REQUEST()]} runId="r_1" canApprove simple />);
    expect(within(screen.getByTestId("approval-card")).queryByRole("button")).toBeNull();
  });

  it("budget warning text for the monthly scope", () => {
    expect(budgetWarningText({ scope: "month", limit_usd: 10, spent_usd: 8.5, fraction: 0.85 })).toBe(
      "This agent has used 85% of its monthly budget ($8.50 of $10.00).",
    );
  });
});

function Harness({ initial, network }: { initial: ApprovalPolicy; network: boolean }) {
  const [p, setP] = useState<ApprovalPolicy>(initial);
  return (
    <>
      <PolicyEditor value={p} onChange={setP} network={network} />
      <pre data-testid="out">{JSON.stringify(normalizePolicy(p))}</pre>
    </>
  );
}

describe("PolicyEditor", () => {
  const out = () => JSON.parse(screen.getByTestId("out").textContent || "{}");

  it("adds presets once, hides network presets when network is off, edits and removes rules", async () => {
    render(<Harness initial={{}} network={false} />);
    expect(screen.queryByRole("button", { name: "curl or wget" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "rm -rf" }));
    await userEvent.click(screen.getByRole("button", { name: "rm -rf" }));
    await userEvent.click(screen.getByRole("button", { name: "Writes to *.env" }));
    expect(out().rules.map((r: { tool: string }) => r.tool)).toEqual(["bash", "write_file", "edit_file"]);
    await userEvent.selectOptions(screen.getByLabelText("Rule 2 mode"), "deny");
    expect(out().rules[1].mode).toBe("deny");
    await userEvent.click(screen.getByRole("button", { name: "Remove rule 1" }));
    expect(out().rules.map((r: { pattern: string }) => r.pattern)).toEqual(["*.env", "*.env"]);
    await userEvent.click(screen.getByRole("button", { name: /Add rule/ }));
    await userEvent.type(screen.getByLabelText("Rule 3 pattern"), "git push");
    expect(out().rules[2]).toEqual({ tool: "bash", pattern: "git push", mode: "ask", label: null });
    await userEvent.selectOptions(screen.getByLabelText("Rule 3 tool"), "*");
    expect(screen.getByLabelText("Rule 3 pattern")).toBeDisabled();
    expect(out().rules[2].pattern).toBeNull();
    const timeout = screen.getByLabelText("Deny automatically after");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "2");
    expect(out().timeout_seconds).toBe(120);
  });

  it("offers curl and wget only with network access", () => {
    render(<Harness initial={{}} network />);
    expect(screen.getByRole("button", { name: "curl or wget" })).toBeInTheDocument();
  });

  it("normalizePolicy and addPreset", () => {
    expect(normalizePolicy(undefined)).toEqual({ rules: [], timeout_seconds: 900 });
    expect(normalizePolicy({ rules: [{ tool: "bash", pattern: " ", mode: "ask" }] }).rules[0].pattern).toBeNull();
    const p = addPreset(addPreset({}, "migrations"), "migrations");
    expect(p.rules).toHaveLength(2);
    expect(PRESETS.map((x) => x.key)).toEqual(["rm_rf", "git_push", "curl_wget", "package_installs", "env_files", "migrations"]);
  });

  it("parseUsd", () => {
    expect(parseUsd("")).toBeNull();
    expect(parseUsd("$1.50")).toBe(1.5);
    expect(parseUsd("0")).toBe(0);
    expect(parseUsd("-1")).toBeUndefined();
    expect(parseUsd("abc")).toBeUndefined();
  });
});

const GOV = (over: Partial<Governance>): Governance => ({
  monthly_budget_usd: 10,
  month_to_date_usd: 8.5,
  fraction: 0.85,
  state: "warning",
  pending_approvals: 0,
  awaiting_runs: [],
  ...over,
});

describe("agent header governance", () => {
  it("budget meter, banner and pending badge", () => {
    const { rerender } = render(
      <MemoryRouter>
        <BudgetMeter gov={GOV({})} />
        <BudgetBanner gov={GOV({})} />
        <PendingApprovalsBadge gov={GOV({ pending_approvals: 1, awaiting_runs: ["r_9"] })} slug="a" />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("budget-meter")).toHaveTextContent("$8.50 of $10.00 this month");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "85");
    expect(screen.getByTestId("budget-banner")).toHaveTextContent("used 85% of its monthly budget");
    expect(screen.getByTestId("pending-approvals")).toHaveAttribute("href", "/runs/r_9");
    expect(screen.getByTestId("pending-approvals")).toHaveTextContent("1 run awaits approval");
    rerender(
      <MemoryRouter>
        <BudgetMeter gov={GOV({ monthly_budget_usd: null, state: "none" })} />
        <BudgetBanner gov={GOV({ state: "ok" })} />
        <PendingApprovalsBadge gov={GOV({})} slug="a" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("budget-meter")).toBeNull();
    expect(screen.queryByTestId("budget-banner")).toBeNull();
    expect(screen.queryByTestId("pending-approvals")).toBeNull();
  });
});

describe("governance statuses", () => {
  it("styles and labels awaiting_approval and budget_exceeded; budget_exceeded is terminal", () => {
    expect(statusClasses("awaiting_approval").pill).toContain("bg-violet-50");
    expect(statusClasses("budget_exceeded").pill).toContain("bg-orange-50");
    render(<StatusPill status="budget_exceeded" />);
    expect(screen.getByTestId("status-pill")).toHaveTextContent("budget exceeded");
    expect(screen.getByTestId("status-pill")).toHaveAttribute("data-status", "budget_exceeded");
    expect(isTerminal("budget_exceeded")).toBe(true);
    expect(isTerminal("awaiting_approval")).toBe(false);
  });
});
