import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { ApprovalPolicy } from "../../src/api";
import PolicyEditor, { RunCapField } from "../../src/components/PolicyEditor";
import { HttpToolModal } from "../../src/components/ToolsConfig";
import { ScheduleForm, SlackForm } from "../../src/pages/agent/TriggersSection";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;
let replies: Record<string, unknown>;
const bodies = (url: string) =>
  fetchMock.mock.calls.filter(([u]) => u === url).map(([, init]) => JSON.parse((init as RequestInit).body as string));

beforeEach(() => {
  replies = {};
  fetchMock = vi.fn(async (url: string) => (url in replies ? json(replies[url]) : json({ detail: `unexpected ${url}` }, 500)));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function Policy() {
  const [p, setP] = useState<ApprovalPolicy>({ rules: [{ tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask" }] });
  return <PolicyEditor value={p} onChange={setP} network slug="demo" tools={["bash", "write_file"]} httpTools={["get_order"]} />;
}

describe("PolicyEditor Suggest rules", () => {
  it("appends new suggested rules, skips duplicates, explains, and Undo restores", async () => {
    replies["/v1/assist/approval_rules"] = {
      result: {
        rules: [
          { tool: "bash", pattern: "\\bgit\\s+push\\b", mode: "ask", label: "git push" },
          { tool: "get_order", pattern: "", mode: "ask", label: "orders API" },
        ],
        explanation: "Network is on, so outbound calls ask first.",
      },
      rationale: "r",
    };
    render(<Policy />);
    await userEvent.click(screen.getByRole("button", { name: "Suggest approval rules" }));
    await waitFor(() => expect(screen.getAllByTestId("policy-rule")).toHaveLength(2));
    expect(screen.getByLabelText("Rule 2 tool")).toHaveValue("get_order");
    expect(screen.getByTestId("rules-explanation")).toHaveTextContent("Network is on");
    expect(bodies("/v1/assist/approval_rules")[0].input).toMatchObject({ tools: ["bash", "write_file"], network: true, http_tools: ["get_order"] });
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getAllByTestId("policy-rule")).toHaveLength(1);
  });
});

function Cap() {
  const [v, setV] = useState("");
  return <RunCapField value={v} onChange={setV} slug="demo" />;
}

describe("RunCapField budget", () => {
  it("fills the cap from the budget suggestion and shows the explanation", async () => {
    replies["/v1/assist/budget"] = {
      result: {
        max_run_cost_usd: 0.4567,
        monthly_budget_usd: 30,
        explanation: "Runs cost 0.12 on average.",
        stats: { runs: 10, mean_usd: 0.12, p95_usd: 0.3, max_usd: 0.35, monthly_usd: 12 },
      },
      rationale: "r",
    };
    render(<Cap />);
    await userEvent.click(screen.getByRole("button", { name: "Suggest a per run cap" }));
    await waitFor(() => expect(screen.getByLabelText("Spending cap per run (USD)")).toHaveValue("0.46"));
    expect(screen.getByTestId("budget-explanation")).toHaveTextContent("Runs cost 0.12 on average.");
  });
});

describe("HttpToolModal Generate", () => {
  it("fills every field from the description and Undo restores the empty form", async () => {
    replies["/v1/assist/http_tool"] = {
      result: {
        name: "get_order",
        description: "Fetch one order by id.",
        method: "GET",
        url: "https://api.example.com/orders/{id}",
        parameters: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
        headers: { Authorization: "Bearer {{API_TOKEN}}" },
        query_params: [],
      },
      rationale: "r",
    };
    const onSave = vi.fn();
    render(<HttpToolModal slug="demo" network initial={null} onSave={onSave} onClose={() => {}} />);
    const generate = screen.getByRole("button", { name: "Generate the tool from this text" });
    expect(generate).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Endpoint source"), "get an order");
    await userEvent.click(generate);
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("get_order"));
    expect(screen.getByLabelText("URL")).toHaveValue("https://api.example.com/orders/{id}");
    expect(screen.getByLabelText("Method")).toHaveValue("GET");
    expect(bodies("/v1/assist/http_tool")[0].input).toEqual({ source: "get an order", existing: [] });
    expect(onSave).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });
});

describe("Trigger forms", () => {
  it("cron: plain English fills cron and timezone and lists the next 3 fire times", async () => {
    replies["/v1/assist/cron"] = {
      result: {
        cron: "30 8 * * 1-5",
        timezone: "America/Los_Angeles",
        explanation: "Weekdays at 8:30.",
        next_fire_times: [1791645000, 1791731400, 1791817800],
      },
      rationale: "r",
    };
    render(<ScheduleForm slug="demo" onDone={() => {}} />);
    await userEvent.type(screen.getByLabelText("Describe the schedule"), "weekdays at 8:30 in LA");
    await userEvent.click(screen.getByRole("button", { name: "Generate the schedule" }));
    await waitFor(() => expect(screen.getByLabelText("Cron expression")).toHaveValue("30 8 * * 1-5"));
    expect(screen.getByLabelText("Time zone")).toHaveValue("America/Los_Angeles");
    const box = screen.getByTestId("cron-suggestion");
    expect(box).toHaveTextContent("Weekdays at 8:30.");
    expect(box.querySelectorAll("li")).toHaveLength(3);
    expect(bodies("/v1/assist/cron")[0].input.text).toBe("weekdays at 8:30 in LA");
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByLabelText("Cron expression")).toHaveValue("0 9 * * 1-5");
  });

  it("slack: Generate fills the input template", async () => {
    replies["/v1/assist/trigger_template"] = { result: { template: "{user} in #{channel} asks: {text}", explanation: "e" }, rationale: "r" };
    render(<SlackForm slug="demo" onDone={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate a template from the event" }));
    await waitFor(() => expect(screen.getByLabelText("Input template")).toHaveValue("{user} in #{channel} asks: {text}"));
    expect(bodies("/v1/assist/trigger_template")[0].input).toEqual({ source: "slack", current: "{text}" });
  });
});
