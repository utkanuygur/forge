import { expect, test, type Locator } from "@playwright/test";
import { createAgent, expectRunSucceeded, RUN_TIMEOUT, uniqueSlug } from "./helpers";

async function expectHelloTranscript(transcript: Locator) {
  const write = transcript.locator('[data-testid="tool-card"][data-tool="write_file"]');
  const bash = transcript.locator('[data-testid="tool-card"][data-tool="bash"]');
  await expect(write).toContainText("● Write(hello.py)", { timeout: RUN_TIMEOUT });
  await expect(write).toContainText("Wrote 1 line to hello.py");
  await expect(bash).toContainText("● Bash(python hello.py)", { timeout: RUN_TIMEOUT });
  await expect(bash).toContainText("⎿");
  await expect(bash).toContainText("hello from forge");
  await expect(bash).toHaveAttribute("data-status", "ok");

  // Narration precedes each tool call; the final answer closes the transcript.
  const narration = transcript.getByTestId("narration");
  await expect(narration.first()).toContainText("I'll create hello.py.");
  await expect(transcript.locator('[data-testid="narration"][data-kind="final"]')).toContainText("Created and ran hello.py.");
  const order = await transcript
    .locator('[data-testid="narration"], [data-testid="tool-card"]')
    .evaluateAll((els) => els.map((e) => e.getAttribute("data-tool") ?? e.getAttribute("data-kind")));
  expect(order).toEqual(["progress", "write_file", "progress", "bash", "final"]);

  await expect(transcript.getByTestId("status-line")).toContainText(/Done in \d+(\.\d)?s · 3 turns/);
}

test("transcript: fake:hello reads like a Claude Code session, on the run page and the share page", async ({ page, browser }) => {
  const slug = uniqueSlug("transcript");
  const agent = await createAgent(page.request, { slug, model: "fake:hello" });

  await page.goto(`/agents/${slug}/runs`);
  await page.getByRole("textbox", { name: "Task" }).fill("Write hello.py and run it");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/r_[0-9a-f]{12}$/);
  const runId = page.url().split("/").pop()!;

  const transcript = page.getByRole("region", { name: "Transcript" });
  await expect(transcript.getByTestId("transcript-input")).toContainText("Write hello.py and run it");
  await expectHelloTranscript(transcript);
  await expectRunSucceeded(page);

  // Ctrl+O expands every tool output (Write shows its content with line numbers).
  await expect(transcript.getByTestId("numbered-code")).toHaveCount(0);
  await page.keyboard.press("Control+o");
  await expect(transcript.getByTestId("numbered-code")).toContainText("print('hello from forge')");
  await expect(transcript.getByTestId("expand-all")).toContainText("Collapse all");
  await page.keyboard.press("Control+o");
  await expect(transcript.getByTestId("numbered-code")).toHaveCount(0);

  // The share page shows the same transcript.
  const ctx = await browser.newContext();
  const colleague = await ctx.newPage();
  await colleague.goto(`/s/${slug}?share=${agent.share_token}&run=${runId}`);
  const shared = colleague.getByRole("region", { name: "Transcript" });
  await expectHelloTranscript(shared);
  await ctx.close();
});
