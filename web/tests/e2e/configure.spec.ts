import { expect, test } from "@playwright/test";
import { createAgent, uniqueSlug } from "./helpers";

test("FR4: save a new version, see it in history, and move Production with Promote", async ({ page, request }) => {
  const slug = uniqueSlug("cfg");
  await createAgent(request, { slug, system_prompt: "Original instructions." });

  await page.goto(`/agents/${slug}/configure`);
  const instructions = page.getByLabel("Instructions");
  await expect(instructions).toHaveValue("Original instructions.");
  const save = page.getByRole("button", { name: "Save as new version" });
  await expect(save).toBeDisabled();

  await instructions.fill("Revised instructions for v2.");
  await page.getByLabel("What changed?").fill("Tighter wording");
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "Saved v2" })).toBeVisible();

  const v1 = page.locator('[data-testid="version-item"][data-version="1"]');
  const v2 = page.locator('[data-testid="version-item"][data-version="2"]');
  await expect(v2).toBeVisible();
  await expect(v2).toContainText("Tighter wording");
  await expect(v1.getByText("Production", { exact: true })).toBeVisible();
  await expect(v2.getByText("Production", { exact: true })).toHaveCount(0);
  await expect(page.getByText("prod v1", { exact: true })).toBeVisible();

  await v2.getByRole("button", { name: "Promote" }).click();
  await expect(v2.getByText("Production", { exact: true })).toBeVisible();
  await expect(v1.getByText("Production", { exact: true })).toHaveCount(0);
  await expect(v1.getByRole("button", { name: "Promote" })).toBeVisible();
  await expect(page.getByText("prod v2", { exact: true })).toBeVisible();
  await expect(instructions).toHaveValue("Revised instructions for v2.");

  await page.reload();
  await expect(page.locator('[data-testid="version-item"][data-version="2"]').getByText("Production", { exact: true })).toBeVisible();
  const prod = await (await request.get(`/v1/agents/${slug}/versions/prod`)).json();
  expect(prod.n).toBe(2);
  expect(prod.system_prompt).toBe("Revised instructions for v2.");
});
