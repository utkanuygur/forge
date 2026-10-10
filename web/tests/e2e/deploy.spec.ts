import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, uniqueSlug } from "./helpers";

test("FR5: deploy tab shows share link and curl; share page runs without exposing configuration", async ({ page, browser }) => {
  const slug = uniqueSlug("share");
  const secretPrompt = `TOP-SECRET-INSTRUCTIONS-${slug}`;
  const name = `Shared ${slug}`;
  const agent = await createAgent(page.request, { slug, name, model: "fake:echo", system_prompt: secretPrompt });

  await page.goto(`/agents/${slug}/deploy`);
  const shareSection = page.locator("section", { has: page.getByRole("heading", { name: "Share link" }) });
  const shareCode = shareSection.locator("code").first();
  await expect(shareCode).toContainText(`/s/${slug}?share=${agent.share_token}`);
  const shareUrl = (await shareCode.textContent())!.trim();
  await expect(shareSection.getByRole("link", { name: "Open" })).toHaveAttribute("href", shareUrl);

  const http = page.locator("section", { has: page.getByRole("heading", { name: "Call over HTTP" }) });
  const curl = http.locator("pre").first();
  await expect(curl).toContainText(`curl -s`);
  await expect(curl).toContainText(`/v1/agents/${slug}/runs`);
  await expect(curl).toContainText(`Authorization: Bearer $FORGE_API_KEY`);

  // A colleague opens the link in a separate browser context (no shared state).
  const ctx = await browser.newContext();
  const colleague = await ctx.newPage();
  await colleague.goto(shareUrl);
  await expect(colleague.getByRole("heading", { level: 1 })).toHaveText(name);
  await expect(colleague.getByRole("tab")).toHaveCount(0);
  await expect(colleague.getByText("Instructions")).toHaveCount(0);
  expect(await colleague.content()).not.toContain(secretPrompt);
  // The agent payload the share page receives carries no configuration either.
  const shared = await colleague.request.get(`/v1/agents/${slug}`, { headers: { "X-Share-Token": agent.share_token } });
  expect(shared.ok()).toBeTruthy();
  expect(await shared.text()).not.toContain(secretPrompt);

  const input = `hello from a colleague ${slug}`;
  await colleague.getByRole("textbox", { name: "Task" }).fill(input);
  await colleague.getByRole("button", { name: "Run", exact: true }).click();
  await expect(colleague).toHaveURL(/[?&]run=r_[0-9a-f]{12}/);
  await expectRunSucceeded(colleague);
  await expect(
    colleague.getByRole("region", { name: "Transcript" }).locator('[data-testid="narration"][data-kind="final"]'),
  ).toContainText(`echo: ${input}`);
  await expect(colleague.getByRole("button", { name: "Timeline" })).toHaveCount(0);
  await expect(colleague.getByRole("region", { name: "Save as test case" })).toHaveCount(0);
  expect(await colleague.content()).not.toContain(secretPrompt);
  await ctx.close();

  // The share run shows up for the builder as a share-sourced run.
  await page.goto(`/agents/${slug}/runs`);
  await expect(page.getByRole("row").filter({ hasText: input })).toContainText("share");
});
