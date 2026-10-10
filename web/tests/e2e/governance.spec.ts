import { expect, test, type APIRequestContext } from "@playwright/test";
import { RUN_TIMEOUT, createAgent, runStatus, uniqueSlug } from "./helpers";

const RM_ASK = {
  rules: [{ tool: "bash", pattern: "\\brm\\s+-\\w*([rR]\\w*f|f\\w*[rR])", mode: "ask", label: "rm -rf" }],
};

async function riskyAgent(request: APIRequestContext, slug: string) {
  const res = await request.post("/v1/agents", {
    data: {
      slug,
      name: `Risky ${slug}`,
      system_prompt: "Clean up build output.",
      model: "fake:risky",
      approval_policy: RM_ASK,
      seed_files: { "build/out.txt": "stale\n" },
    },
  });
  expect(res.ok(), await res.text()).toBeTruthy();
}

async function startRun(request: APIRequestContext, slug: string): Promise<string> {
  const res = await request.post(`/v1/agents/${slug}/runs`, { data: { input: "clean the build" } });
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()).id;
}

test("GOV10: approval card appears live, shows the matched rule, and Approve lets the call run", async ({ page, request }) => {
  const slug = uniqueSlug("gov-ok");
  await riskyAgent(request, slug);
  const runId = await startRun(request, slug);

  await page.goto(`/agents/${slug}/configure`);
  await expect(page.getByTestId("pending-approvals")).toContainText("1 run awaits approval", { timeout: RUN_TIMEOUT });
  await expect(page.getByTestId("tab-badge-runs")).toHaveText("1");
  await page.getByTestId("pending-approvals").click();
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));

  const card = page.getByTestId("approval-card");
  await expect(card).toHaveAttribute("data-state", "pending", { timeout: RUN_TIMEOUT });
  await expect(card).toContainText("rm -rf build");
  await expect(card).toContainText("rm -rf: bash matching");
  await expect(runStatus(page)).toHaveAttribute("data-status", "awaiting_approval");
  await card.getByRole("button", { name: "Approve" }).click();

  await expect(card).toHaveAttribute("data-state", "approved", { timeout: RUN_TIMEOUT });
  await expect(runStatus(page)).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  await expect(page.getByText("Removed the build directory.").last()).toBeVisible();
});

test("GOV10: Deny with a note sends the note back to the agent and the run continues", async ({ page, request }) => {
  const slug = uniqueSlug("gov-deny");
  await riskyAgent(request, slug);
  const runId = await startRun(request, slug);
  await page.goto(`/runs/${runId}`);

  const card = page.getByTestId("approval-card");
  await expect(card).toHaveAttribute("data-state", "pending", { timeout: RUN_TIMEOUT });
  await card.getByLabel("Reviewer note").fill("keep the build for now");
  await card.getByRole("button", { name: "Deny" }).click();
  await expect(card).toHaveAttribute("data-state", "denied", { timeout: RUN_TIMEOUT });
  await expect(card).toContainText("keep the build for now");
  await expect(runStatus(page)).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  // The transcript (default run view) ends with the final answer, which quotes the denial.
  const transcript = page.getByTestId("transcript");
  await expect(transcript.locator('[data-kind="final"]')).toContainText("Denied by reviewer: keep the build for now");
});

test("GOV11: policy editor presets, run cap and monthly budget save; a spent budget shows a banner and refuses runs", async ({
  page,
  request,
}) => {
  const slug = uniqueSlug("gov-cfg");
  await createAgent(request, { slug, model: "fake:echo" });
  await page.goto(`/agents/${slug}/configure`);

  const editor = page.getByTestId("policy-editor");
  await expect(editor).toContainText("No rules.");
  await expect(editor.getByRole("button", { name: "curl or wget" })).toHaveCount(0);
  await editor.getByRole("button", { name: "rm -rf" }).click();
  await editor.getByRole("button", { name: "Writes to migrations/**" }).click();
  await expect(page.getByTestId("policy-rule")).toHaveCount(3);
  await page.getByLabel("Rule 3 mode").selectOption("deny");
  await page.getByLabel("Spending cap per run (USD)").fill("0.50");
  await page.getByRole("button", { name: "Save as new version" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved v2" })).toBeVisible();

  const v2 = await (await request.get(`/v1/agents/${slug}/versions/2`)).json();
  expect(v2.max_run_cost_usd).toBe(0.5);
  expect(v2.approval_policy.rules.map((r: { tool: string; mode: string }) => `${r.tool}:${r.mode}`)).toEqual([
    "bash:ask",
    "write_file:ask",
    "edit_file:deny",
  ]);

  await page.getByLabel("Monthly budget (USD)").fill("5");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("budget-meter")).toContainText("$0.00 of $5.00 this month");

  await page.getByLabel("Monthly budget (USD)").fill("0");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("budget-banner")).toContainText("Monthly budget used up");
  await expect(page.getByTestId("budget-meter")).toHaveAttribute("data-state", "exceeded");

  await page.getByRole("tab", { name: "Runs" }).click();
  await page.getByLabel("Task").fill("hello");
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/Monthly budget of \$0.00 reached/)).toBeVisible();
});
