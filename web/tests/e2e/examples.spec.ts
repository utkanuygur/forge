import { expect, test } from "@playwright/test";
import { hasNoHorizontalScroll } from "./helpers";

// Runs against the second server from playwright.config.ts, which starts with no agents.
test.use({ baseURL: process.env.FORGE_E2E_EMPTY_URL });

test.describe.serial("Example agents", () => {
  test("Add all on an empty server installs the examples, then Log triage opens with its prompts", async ({ page, request }) => {
    expect(await (await request.get("/v1/agents")).json()).toEqual([]);
    await page.goto("/");
    const card = page.getByTestId("examples-empty-card");
    await expect(card).toContainText("Four ready to run agents with test cases");
    await card.getByRole("button", { name: "Add all" }).click();

    await expect(page.getByRole("status")).toContainText("Added 4 example agents");
    await expect(page.getByTestId("agent-card")).toHaveCount(4);
    await expect(card).toHaveCount(0);

    await page.locator('[data-testid="agent-card"][data-slug="log-triage"]').click();
    await expect(page).toHaveURL(/\/agents\/log-triage/);
    await expect(page.getByRole("heading", { level: 1, name: "Log triage" })).toBeVisible();
    await page.getByRole("tab", { name: "Runs" }).click();
    await expect(page.getByText("Summarize today's incidents").first()).toBeVisible();

    const evals = await (await request.get("/v1/agents/log-triage/evals")).json();
    expect(evals).toHaveLength(4);
  });

  test("the Examples modal marks installed examples and fits at 390px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("button", { name: "Examples" }).click();
    const dialog = page.getByRole("dialog", { name: "Example agents" });
    await expect(dialog.getByTestId("example-row")).toHaveCount(4);
    await expect(dialog.getByText("Installed")).toHaveCount(4);
    await expect(dialog.getByLabel("Model")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Add all" })).toBeDisabled();
    const m = await hasNoHorizontalScroll(page);
    expect(m.scrollWidth, "examples modal").toBeLessThanOrEqual(m.clientWidth);

    await dialog.getByRole("link", { name: "Open Docs writer" }).click();
    await expect(page).toHaveURL(/\/agents\/docs-writer/);
  });
});
