import { expect, test } from "@playwright/test";
import { createAgent, expectRunSucceeded, hasNoHorizontalScroll, runViaApi, uniqueSlug } from "./helpers";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("responsive: agents page and run page have no horizontal scroll at 390px", async ({ page, request }) => {
  const slug = uniqueSlug("mobile-agent-with-a-rather-long-slug");
  await createAgent(request, {
    slug,
    name: "Mobile agent with a fairly long display name to stress wrapping",
    description: "A long description ".repeat(10),
    model: "fake:hello",
  });
  const run = await runViaApi(request, slug, "Write hello.py and run it, then report back with a long explanation.");

  await page.goto("/");
  await expect(page.locator(`[data-testid="agent-card"][data-slug="${slug}"]`)).toBeVisible();
  let m = await hasNoHorizontalScroll(page);
  expect(m.scrollWidth, "agents page").toBeLessThanOrEqual(m.clientWidth);

  await page.getByRole("button", { name: "New agent" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  m = await hasNoHorizontalScroll(page);
  expect(m.scrollWidth, "new agent modal").toBeLessThanOrEqual(m.clientWidth);
  await page.getByRole("button", { name: "Close" }).click();

  await page.goto(`/runs/${run.id}`);
  await expectRunSucceeded(page);
  await expect(page.getByTestId("diff-view")).toBeVisible();
  await page.locator('[data-testid="tool-card"][data-tool="write_file"]').getByTestId("tool-header").click();
  await expect(page.getByTestId("numbered-code")).toBeVisible();
  m = await hasNoHorizontalScroll(page);
  expect(m.scrollWidth, "run page").toBeLessThanOrEqual(m.clientWidth);
});
