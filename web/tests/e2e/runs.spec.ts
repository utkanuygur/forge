import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, RUN_TIMEOUT, runViaApi, uniqueSlug } from "./helpers";

test.describe("Runs", () => {
  test("FR2 FR6 FR7 NFR3: run streams a live trace, succeeds, shows the diff and artifacts", async ({ page, request }) => {
    const slug = uniqueSlug("run");
    await createAgent(request, { slug, model: "fake:hello" });

    await page.goto(`/agents/${slug}/runs`);
    await expect(page.getByText("No runs yet")).toBeVisible();
    await page.getByRole("textbox", { name: "Task" }).fill("Write hello.py and run it");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page).toHaveURL(/\/runs\/r_[0-9a-f]{12}$/);
    const runId = page.url().split("/").pop()!;

    // Live transcript over SSE: tool blocks appear without a reload.
    const transcript = page.getByRole("region", { name: "Transcript" });
    await expect(transcript.locator('[data-testid="tool-card"][data-tool="write_file"]')).toBeVisible({ timeout: RUN_TIMEOUT });
    const bash = transcript.locator('[data-testid="tool-card"][data-tool="bash"]');
    await expect(bash).toBeVisible({ timeout: RUN_TIMEOUT });
    await expect(bash).toContainText("python hello.py");
    await expectRunSucceeded(page);
    await expect(transcript.getByTestId("status-line")).toContainText("Done in");

    // Short bash output is shown inline under the header.
    await expect(bash).toContainText("hello from forge");
    await expect(bash).not.toHaveAttribute("data-error", "true");
    await expect(transcript.locator('[data-testid="narration"][data-kind="final"]')).toContainText("Created and ran hello.py.");

    // The timeline view is still one click away.
    await page.getByRole("button", { name: "Timeline" }).click();
    const trace = page.getByRole("region", { name: "Trace" });
    await expect(trace).toContainText("Finished succeeded");
    const timelineBash = trace.locator('[data-testid="tool-card"][data-tool="bash"]');
    await timelineBash.getByRole("button").click();
    await expect(timelineBash).toContainText("hello from forge");
    await expect(page.getByRole("region", { name: "Output" })).toContainText("Created and ran hello.py.");
    await page.getByRole("button", { name: "Transcript" }).click();
    await expect(transcript).toBeVisible();

    const changes = page.getByRole("region", { name: "Changes" });
    const diff = changes.getByTestId("diff-view");
    await expect(diff).toContainText("hello.py");
    await expect(diff.locator('[data-kind="add"]', { hasText: "print('hello from forge')" })).toBeVisible();

    const artifact = changes.getByRole("link", { name: /Workspace \.tar\.gz/ });
    await expect(artifact).toHaveAttribute("href", `/v1/runs/${runId}/artifacts`);
    const res = await request.get(`/v1/runs/${runId}/artifacts`);
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("gzip");
    const body = await res.body();
    expect(body[0]).toBe(0x1f);
    expect(body[1]).toBe(0x8b);

    // The run is listed on the Runs tab.
    await page.goto(`/agents/${slug}/runs`);
    await expect(page.getByRole("row").filter({ hasText: "Write hello.py and run it" })).toContainText("succeeded");
  });

  test("FR8: thumbs down with a comment persists after reload", async ({ page, request }) => {
    const slug = uniqueSlug("fb");
    await createAgent(request, { slug, model: "fake:echo" });
    const run = await runViaApi(request, slug, "say something");

    await page.goto(`/runs/${run.id}`);
    await expectRunSucceeded(page);
    const feedback = page.getByRole("region", { name: "Feedback" });
    await feedback.getByRole("button", { name: "Needs work" }).click();
    await expect(feedback.getByRole("button", { name: "Needs work" })).toHaveAttribute("aria-pressed", "true");
    const comment = `Too terse ${slug}`;
    await feedback.getByRole("textbox", { name: "Feedback comment" }).fill(comment);
    await feedback.getByRole("button", { name: "Send feedback" }).click();
    await expect(feedback.getByText(comment)).toBeVisible();

    await page.reload();
    await expect(page.getByRole("region", { name: "Feedback" }).getByText(comment)).toBeVisible();

    const saved = await (await request.get(`/v1/runs/${run.id}`)).json();
    expect(saved.feedback).toEqual([expect.objectContaining({ rating: -1, comment })]);

    await page.goto(`/agents/${slug}/runs`);
    const row = page.getByRole("row").filter({ hasText: "say something" });
    await expect(row.locator(".text-red-600")).toContainText("1");
  });

  test("FR11: save a run as a test case and see it on the Evaluations tab", async ({ page, request }) => {
    const slug = uniqueSlug("save");
    await createAgent(request, { slug, model: "fake:echo" });
    const run = await runViaApi(request, slug, "turn me into a test case");

    await page.goto(`/runs/${run.id}`);
    await expectRunSucceeded(page);
    const panel = page.getByRole("region", { name: "Save as test case" });
    const caseName = `case-${slug}`;
    await panel.getByRole("textbox", { name: "Case name" }).fill(caseName);
    await panel.getByRole("button", { name: "holdout" }).click();
    await panel.getByRole("button", { name: "Save as test case" }).click();
    await expect(page.getByRole("status").filter({ hasText: `Saved test case "${caseName}" (holdout)` })).toBeVisible();

    await page.goto(`/agents/${slug}/evals`);
    const row = page.locator(`[data-testid="eval-case"][data-name="${caseName}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText("holdout");
    await expect(row).toContainText("judge");
    await expect(row).toContainText("turn me into a test case");
  });
});
