import { expect, test } from "@playwright/test";
import { uniqueSlug } from "./helpers";

test.describe("Create agents", () => {
  test("FR1: create from blank form shows the API key once and lists the agent", async ({ page }) => {
    const slug = uniqueSlug("blank");
    const name = `Blank ${slug}`;
    await page.goto("/");
    await page.getByRole("button", { name: "New agent" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: "Blank" })).toBeVisible();
    await dialog.getByLabel("Name").fill(name);
    await dialog.getByLabel("Slug").fill(slug);
    await dialog.getByLabel("Instructions").fill("You are a blank test agent.");
    await dialog.getByRole("button", { name: "Create agent" }).click();
    await expect(dialog).toBeHidden();

    const callout = page.getByTestId("api-key-callout");
    await expect(callout).toBeVisible();
    await expect(callout).toContainText(`API key for ${name}`);
    await expect(callout).toContainText("It will not be shown again");
    const key = (await callout.getByTestId("api-key").textContent())?.trim() ?? "";
    expect(key.length).toBeGreaterThan(16);

    const card = page.locator(`[data-testid="agent-card"][data-slug="${slug}"]`);
    await expect(card).toBeVisible();
    await expect(card).toContainText(name);
    await expect(card).toContainText("prod v1");

    // The key really works as a bearer token.
    const res = await page.request.get(`/v1/agents/${slug}/runs`, { headers: { Authorization: `Bearer ${key}` } });
    expect(res.status()).toBe(200);

    // Shown once: after a reload it is gone and appears nowhere on the page.
    await page.reload();
    await expect(card).toBeVisible();
    await expect(page.getByTestId("api-key-callout")).toHaveCount(0);
    expect(await page.content()).not.toContain(key);
    await page.goto(`/agents/${slug}/deploy`);
    await expect(page.getByRole("button", { name: "Rotate key" })).toBeVisible();
    expect(await page.content()).not.toContain(key);
  });

  test("FR1: create from a template prefills the form", async ({ page, request }) => {
    const templates: { key: string; name: string; description: string; system_prompt: string; tools: string[] }[] = await (
      await request.get("/v1/templates")
    ).json();
    expect(templates.length).toBeGreaterThan(0);
    const t = templates[0];
    const slug = uniqueSlug("tpl");

    await page.goto("/");
    await page.getByRole("button", { name: "New agent" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: t.name, exact: true }).click();
    await expect(dialog.getByLabel("Name")).toHaveValue(t.name);
    await expect(dialog.getByLabel("Description")).toHaveValue(t.description);
    await expect(dialog.getByLabel("Instructions")).toHaveValue(t.system_prompt);
    for (const tool of ["bash", "read_file", "write_file", "edit_file"]) {
      const box = dialog.getByRole("checkbox", { name: tool });
      if (t.tools.includes(tool)) await expect(box).toBeChecked();
      else await expect(box).not.toBeChecked();
    }
    await dialog.getByLabel("Slug").fill(slug);
    await dialog.getByRole("button", { name: "Create agent" }).click();
    await expect(page.getByTestId("api-key-callout")).toBeVisible();

    await page.locator(`[data-testid="agent-card"][data-slug="${slug}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/agents/${slug}$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(t.name);
    await expect(page.getByLabel("Instructions")).toHaveValue(t.system_prompt);
  });
});
