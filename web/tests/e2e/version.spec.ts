import { expect, test } from "@playwright/test";

// The test server runs from this checkout, so it reports channel dev and the checkout's commit.
test("header shows the running version next to the wordmark", async ({ page, request }) => {
  const info = await (await request.get("/v1/version")).json();
  expect(info.channel).toBe("dev");
  expect(info.commit).toMatch(/^[0-9a-f]{7,}$/);

  await page.goto("/");
  const badge = page.locator("header").getByTestId("version-badge");
  await expect(badge).toHaveText(`dev · ${info.commit}`);
  await expect(badge).toHaveAccessibleName(new RegExp(`^Forge version .*, dev, commit ${info.commit}$`));

  await badge.hover();
  const tip = page.getByRole("tooltip");
  await expect(tip).toContainText("Channeldev");
  await expect(tip).toContainText(`Commit${info.commit}`);
  await expect(tip.getByRole("link", { name: "What's new" })).toHaveCount(0);
});

test.describe("narrow screens", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("keep the version and hide the commit", async ({ page }) => {
    await page.goto("/");
    const badge = page.locator("header").getByTestId("version-badge");
    await expect(badge.getByTestId("version-badge-version")).toBeVisible();
    await expect(badge.getByTestId("version-badge-commit")).toBeHidden();
  });
});
