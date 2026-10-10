import { expect, test, type APIRequestContext } from "@playwright/test";
import { createAgent, hasNoHorizontalScroll, RUN_TIMEOUT, runViaApi, uniqueSlug } from "./helpers";

async function run(request: APIRequestContext, slug: string, input: string, version?: number) {
  const res = await request.post(`/v1/agents/${slug}/runs`, { data: { input, wait: true, ...(version ? { version } : {}) }, timeout: RUN_TIMEOUT });
  expect(res.ok(), await res.text()).toBeTruthy();
  return res.json();
}

async function newVersion(request: APIRequestContext, slug: string, data: Record<string, unknown>): Promise<number> {
  const res = await request.post(`/v1/agents/${slug}/versions`, { data });
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()).n;
}

/** An agent with v1 (fake:hello, 2 runs, one disliked) and v2 (fake:error, 1 failed run). */
async function agentWithHistory(request: APIRequestContext, prefix: string) {
  const slug = uniqueSlug(prefix);
  await createAgent(request, { slug, model: "fake:hello", name: `Obs ${prefix}` });
  const a = await runViaApi(request, slug, `first task ${slug}`);
  const b = await runViaApi(request, slug, "needle-in-the-input task");
  await request.post(`/v1/runs/${b.id}/feedback`, { data: { rating: -1, comment: "missed the point" } });
  const v2 = await newVersion(request, slug, { model: "fake:error" });
  const failed = await run(request, slug, "this one fails", v2);
  expect(failed.status).toBe("failed");
  return { slug, a, b, failed, v2 };
}

test.describe("Observability", () => {
  test("OBS1 OBS2: dashboard shows KPIs, charts and the agent row after a few fake runs", async ({ page, request }) => {
    const { slug } = await agentWithHistory(request, "dash");
    await page.goto("/");
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    const kpis = page.getByTestId("kpi");
    await expect(kpis).toHaveCount(5);
    const runs = Number((await page.locator('[data-kpi="runs"] [data-testid="kpi-value"]').textContent())?.replace(/[^0-9]/g, ""));
    expect(runs).toBeGreaterThanOrEqual(3);
    await expect(page.locator('[data-kpi="cost_usd"] [data-testid="kpi-value"]')).toContainText("$");
    const runsChart = page.getByTestId("chart-runs");
    await expect(runsChart.locator('[data-testid="bar"]:not([data-total="0"])').first()).toBeVisible();
    await expect(page.getByTestId("chart-spend").locator("svg")).toBeVisible();
    const row = page.locator(`[data-testid="dashboard-agent"][data-slug="${slug}"]`);
    await expect(row).toBeVisible();
    await expect(row.getByTestId("sparkline")).toBeVisible();
    await expect(row).toContainText("67%"); // 2 of 3 runs succeeded
    await expect(page.getByRole("region", { name: "Top failure reasons" })).toContainText("RuntimeError: fake model failure");
  });

  test("OBS1 OBS4 OBS6: Monitoring tab compares versions with prod highlighted and marks the promotion", async ({ page, request }) => {
    const { slug, v2 } = await agentWithHistory(request, "mon");
    const v3 = await newVersion(request, slug, { model: "fake:hello", rationale: "back to hello" });
    await runViaApi(request, slug, "after the fix");
    expect((await request.post(`/v1/agents/${slug}/promote`, { data: { version: v3 } })).ok()).toBeTruthy();
    await runViaApi(request, slug, "on the new prod");

    await page.goto(`/agents/${slug}`);
    await page.getByRole("tab", { name: "Monitoring" }).click();
    await expect(page).toHaveURL(new RegExp(`/agents/${slug}/monitoring$`));
    const table = page.getByTestId("version-table");
    const prod = table.locator(`[data-testid="version-row"][data-version="${v3}"]`);
    await expect(prod).toHaveAttribute("data-prod", "true");
    await expect(prod).toContainText("prod");
    await expect(table.locator('[data-testid="version-row"][data-version="1"]')).toContainText("previous prod");
    await expect(prod.getByTestId("version-delta").first()).toBeVisible();
    await expect(table.locator(`[data-testid="version-row"][data-version="${v2}"]`)).toContainText("0%");
    const marker = page.getByTestId("monitor-runs-chart").locator(`[data-testid="promotion-marker"][data-label="v${v3}"]`);
    await expect(marker).toHaveCount(1);
    await expect(page.getByTestId("failure-categories")).toContainText("Model error");
    await expect(page.getByTestId("failure-reasons")).toContainText("missed the point");
    await expect(page.getByTestId("failure-reasons").getByTestId("example-run").first()).toHaveAttribute("href", /\/runs\/r_/);
    await expect(page.locator('[data-testid="tool-row"][data-tool="bash"]')).toBeVisible();
  });

  test("OBS5: Runs tab filters narrow the results and show the count", async ({ page, request }) => {
    const { slug, b, failed } = await agentWithHistory(request, "filt");
    await page.goto(`/agents/${slug}/runs`);
    await expect(page.getByTestId("run-count")).toHaveText("3 runs");
    await expect(page.getByTestId("run-row")).toHaveCount(3);

    await page.getByRole("combobox", { name: "Status filter" }).selectOption("failed");
    await expect(page.getByTestId("run-count")).toHaveText("1 run");
    await expect(page.getByTestId("run-row")).toHaveCount(1);
    await expect(page.getByTestId("run-row")).toContainText("this one fails");
    await expect(page).toHaveURL(/status=failed/);
    await page.getByTestId("run-row").click();
    await expect(page).toHaveURL(new RegExp(`/runs/${failed.id}$`));
    await page.goBack();

    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(page.getByTestId("run-row")).toHaveCount(3);
    await page.getByRole("searchbox", { name: "Search runs" }).fill("needle-in-the");
    await expect(page.getByTestId("run-row")).toHaveCount(1);
    await expect(page.getByTestId("run-row")).toContainText("needle-in-the-input task");
    await page.getByRole("searchbox", { name: "Search runs" }).fill("");
    await page.getByRole("combobox", { name: "Rating filter" }).selectOption("down");
    await expect(page.getByTestId("run-row")).toHaveCount(1);
    await page.getByRole("combobox", { name: "Rating filter" }).selectOption("");
    await page.getByRole("combobox", { name: "Version filter" }).selectOption("1");
    await expect(page.getByTestId("run-count")).toHaveText("2 runs");
    await page.getByRole("combobox", { name: "Sort filter" }).selectOption("cost");
    await expect(page.getByTestId("run-row")).toHaveCount(2);
    expect(b.id).toMatch(/^r_/);
  });

  test("OBS3 OBS2: run view shows the step timeline and the run's cost", async ({ page, request }) => {
    const slug = uniqueSlug("tl");
    await createAgent(request, { slug, model: "fake:hello" });
    const r = await runViaApi(request, slug, "write hello and run it");
    await page.goto(`/runs/${r.id}`);
    const panel = page.getByRole("region", { name: "Step timeline" });
    const tl = panel.getByTestId("step-timeline");
    await expect(tl).toBeVisible();
    await expect(tl).toHaveAttribute("data-estimated", "false");
    await expect(tl.getByTestId("timeline-step")).toHaveCount(5);
    await expect(tl.locator('[data-testid="timeline-step"][data-kind="tool"][data-label="bash"]')).toContainText("ms");
    await expect(tl.locator('[data-testid="timeline-step"][data-kind="model"]').first()).toContainText("10/5 tok");
    await expect(tl.getByTestId("tool-time")).toContainText("ms");
    await expect(tl.getByTestId("run-cost")).toContainText("$0.06");
    await expect(page.getByTestId("run-header-cost")).toContainText("$0.06");
  });
});

test.describe("Observability at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("responsive: dashboard, monitoring, runs and run timeline have no horizontal page scroll at 390px", async ({ page, request }) => {
    const { slug, a } = await agentWithHistory(request, "mobile-observability-agent-with-a-long-slug");
    for (const path of ["/dashboard", `/agents/${slug}/monitoring`, `/agents/${slug}/runs`, `/runs/${a.id}`]) {
      await page.goto(path);
      if (path === "/dashboard") await expect(page.getByTestId("kpi")).toHaveCount(5);
      if (path.endsWith("/monitoring")) await expect(page.getByTestId("version-table")).toBeVisible();
      if (path.endsWith("/runs")) await expect(page.getByTestId("run-row").first()).toBeVisible();
      if (path.startsWith("/runs/")) await expect(page.getByTestId("timeline-step").first()).toBeVisible();
      const m = await hasNoHorizontalScroll(page);
      expect(m.scrollWidth, path).toBeLessThanOrEqual(m.clientWidth);
    }
  });
});
