import { createHmac } from "node:crypto";
import { expect, test } from "@playwright/test";
import { createAgent, RUN_TIMEOUT, uniqueSlug } from "./helpers";

test("TRG8: create a schedule with a live preview, toggle it and fire a test run", async ({ page }) => {
  const slug = uniqueSlug("sched");
  await createAgent(page.request, { slug, model: "fake:echo" });
  await page.goto(`/agents/${slug}/deploy`);
  const section = page.locator("section", { has: page.getByRole("heading", { name: "Triggers" }) });
  await expect(section.getByText("No triggers yet.")).toBeVisible();
  await section.getByRole("button", { name: "Add schedule" }).click();

  const form = page.getByRole("region", { name: "New Schedule trigger" });
  await expect(form.getByLabel("Preset")).toHaveValue("0 9 * * 1-5");
  await form.getByLabel("Time zone").fill("UTC");
  const preview = form.getByRole("region", { name: "Schedule preview" });
  await expect(preview).toContainText("Every weekday at 9:00");
  await expect(preview.locator("li")).toHaveCount(3);
  await form.getByLabel("Preset").selectOption({ label: "Every hour" });
  await expect(form.getByLabel("Cron expression")).toHaveValue("0 * * * *");
  await expect(preview).toContainText("Every hour");
  await form.getByLabel("Cron expression").fill("61 * * * *");
  await expect(form.getByText(/minute value 61 out of range/)).toBeVisible();
  await form.getByLabel("Cron expression").fill("30 7 * * 1");
  await expect(preview).toContainText("Every Monday at 7:30");
  await form.getByLabel("Input").fill(`Weekly digest for {date} ${slug}`);
  await form.getByRole("button", { name: "Save schedule" }).click();

  const card = page.getByTestId("trigger-card");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("Every Monday at 7:30 (30 7 * * 1, UTC)");
  await expect(card.getByTestId("last-fired")).toHaveText("Never");
  const toggle = card.getByRole("switch");
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await expect(card).toContainText("off");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");

  await card.getByRole("button", { name: "Test" }).click();
  const recent = card.getByRole("region", { name: "Recent runs" });
  await expect(recent.getByRole("link", { name: /^r_[0-9a-f]{12}$/ })).toBeVisible();
  await expect(recent).toContainText("test");
  await expect(recent.getByTestId("status-pill")).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });

  // persisted: a reload shows the same trigger, now with a last fired time
  await page.reload();
  await expect(page.getByTestId("trigger-card")).toContainText("Every Monday at 7:30");
  await expect(page.getByTestId("last-fired")).not.toHaveText("Never");
});

test("TRG8: create a GitHub trigger, see the secret once, and a signed delivery starts a run", async ({ page }) => {
  const slug = uniqueSlug("gh");
  await createAgent(page.request, { slug, model: "fake:echo" });
  await page.goto(`/agents/${slug}/deploy`);
  const section = page.locator("section", { has: page.getByRole("heading", { name: "Triggers" }) });
  await section.getByRole("button", { name: "Add GitHub" }).click();

  const form = page.getByRole("region", { name: "New GitHub trigger" });
  await form.getByLabel("Pushes").check();
  await form.getByLabel("Label filter").fill("forge");
  await form.getByLabel("Comment command").fill("/agent");
  await form.getByLabel("Input template").fill("Issue {repo}#{number}: {title}");
  await form.getByRole("button", { name: "Save GitHub trigger" }).click();

  const card = page.getByTestId("trigger-card");
  await expect(card).toContainText("issues, issue_comment, pull_request, push");
  await expect(card).toContainText("label forge");
  await expect(card).toContainText("command /agent");
  const secret = (await card.getByTestId("webhook-secret").textContent())!.trim();
  expect(secret).toMatch(/^whsec_[0-9a-f]{48}$/);
  const url = (await card.getByLabel("Payload URL").textContent())!.trim();
  const origin = new URL(page.url()).origin;
  expect(url).toMatch(new RegExp(`^${origin}/v1/hooks/github/tr_[0-9a-f]{12}$`));

  const payload = JSON.stringify({
    action: "labeled",
    label: { name: "forge" },
    repository: { full_name: "octo/app" },
    sender: { login: "mona", type: "User" },
    issue: { number: 12, title: `Broken build ${slug}`, body: "", html_url: "https://github.com/octo/app/issues/12" },
  });
  const sig = "sha256=" + createHmac("sha256", secret).update(payload).digest("hex");
  const unsigned = await page.request.post(url, { data: payload, headers: { "X-GitHub-Event": "issues", "Content-Type": "application/json" } });
  expect(unsigned.status()).toBe(401);
  const res = await page.request.post(url, {
    data: payload,
    headers: { "X-GitHub-Event": "issues", "Content-Type": "application/json", "X-Hub-Signature-256": sig },
  });
  expect(res.status(), await res.text()).toBe(202);
  const { run_id } = await res.json();

  const recent = card.getByRole("region", { name: "Recent runs" });
  await expect(recent.getByRole("link", { name: run_id })).toBeVisible({ timeout: 15_000 });
  await expect(recent.getByTestId("status-pill")).toHaveAttribute("data-status", "succeeded", { timeout: RUN_TIMEOUT });
  await recent.getByRole("link", { name: run_id }).click();
  await expect(page.getByTestId("transcript").locator('[data-kind="final"]')).toContainText(`echo: Issue octo/app#12: Broken build ${slug}`);

  // the secret is never shown again
  await page.goto(`/agents/${slug}/deploy`);
  await expect(page.getByTestId("trigger-card")).toBeVisible();
  await expect(page.getByTestId("webhook-secret")).toHaveCount(0);
  expect(await page.content()).not.toContain(secret);
});
