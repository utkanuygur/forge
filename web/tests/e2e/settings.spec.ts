import { expect, test } from "@playwright/test";
import { createAgent, hasNoHorizontalScroll, runViaApi, uniqueSlug } from "./helpers";

// Runs against its own server (project "settings" in playwright.config.ts) because it changes server wide
// settings. Tests here run one after another so they never see each other's changes half way.
test.describe.configure({ mode: "serial" });

test.afterAll(async ({ request }) => {
  await request.post("/v1/settings/reset", { data: { keys: ["default_model", "max_concurrent_runs"] } });
});

test("SET1: change the default model in Settings and a new agent uses it", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByTestId("source-default_model")).toHaveText("Default");

  const field = page.getByLabel("Default agent model", { exact: true });
  await expect(field).toHaveValue("anthropic:claude-sonnet-5-5");
  await field.fill("fake:echo");
  await page.getByRole("button", { name: "Save Models" }).click();
  await expect(page.getByTestId("source-default_model")).toHaveText("Saved");
  await page.reload();
  await expect(page.getByLabel("Default agent model", { exact: true })).toHaveValue("fake:echo");

  // A new agent that does not pick a model gets the new default, without a server restart.
  const slug = uniqueSlug("setdef");
  await createAgent(request, { slug });
  const prod = await (await request.get(`/v1/agents/${slug}/versions/prod`)).json();
  expect(prod.model).toBe("fake:echo");
  await page.goto(`/agents/${slug}/configure`);
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue("fake:echo");
  const run = await runViaApi(request, slug, "ping");
  expect((await (await request.get(`/v1/runs/${run.id}`)).json()).output).toBe("echo: ping");

  // Back to the default from the field's own button.
  await page.goto("/settings");
  await page.getByRole("button", { name: "Reset Default agent model to default" }).click();
  await expect(page.getByTestId("source-default_model")).toHaveText("Default");
});

test("SET2: a restart setting shows Restart required, and keys show their status", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("restart-banner")).toHaveCount(0);
  const field = page.getByLabel("Max concurrent runs", { exact: true });
  await field.fill("3");
  await page.getByRole("button", { name: "Save Server" }).click();
  const banner = page.getByTestId("restart-banner");
  await expect(banner).toContainText("Restart required");
  await expect(banner.getByRole("button", { name: "Restart" })).toHaveCount(0); // only inside the desktop app
  await expect(page.getByTestId("setting-max_concurrent_runs")).toContainText("Restart required");

  await expect(page.getByTestId("secret-anthropic_api_key")).toContainText("Not set");
  await expect(page.getByTestId("status-claude")).toContainText("Not set");
  await expect(page.getByTestId("status-docker")).toContainText("Running");

  await page.getByRole("button", { name: "Reset Max concurrent runs to default" }).click();
  await expect(page.getByTestId("restart-banner")).toHaveCount(0);
});

test("SET3: invalid values are refused inline and the page fits a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  const repeats = page.getByLabel("Default eval repeats", { exact: true });
  await repeats.fill("12");
  await page.getByRole("button", { name: "Save Evaluations" }).click();
  await expect(page.getByTestId("error-eval_repeats")).toHaveText("must be at most 9");
  const url = page.getByLabel("Public URL", { exact: true });
  await url.fill("forge.example.com");
  await page.getByRole("button", { name: "Save Sharing" }).click();
  await expect(page.getByTestId("error-public_url")).toContainText("http or https URL");
  const { scrollWidth, clientWidth } = await hasNoHorizontalScroll(page);
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
});
